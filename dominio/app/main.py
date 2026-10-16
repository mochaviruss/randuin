"""Microservicio de dominio de Randuin (la API de Decision de la EP1).

Es el UNICO servicio que lee y escribe la base. Para cada transaccion:
  1. Valida la entrada (Pydantic: si no cumple, 422) y que el usuario exista (si no, 404).
  2. Lista negra (RF4, RF8): si el dispositivo o el comercio estan en ella, bloquea sin consultar al modelo.
  3. Calcula las variables del modelo con datos de la base (velocidad RF10, dispositivo, hora, pais, canal).
  4. Pide la probabilidad de fraude al servicio de IA A TRAVES DEL GATEWAY (que reparte entre las 2 replicas),
     con un tope de 100 ms (RNF3). Si no responde, aplica la politica de reserva.
  5. Decide con los umbrales vigentes de Riesgo (RF3) y guarda la prediccion completa (RF7).

Endpoints (el gateway los publica bajo /api/decision):
  GET  /health                       200 si la base responde, 503 si no
  POST /evaluar                      decision, score, razones, version del modelo y replica que respondio
  GET  /predicciones                 las ultimas decisiones (las muestra el front)
  GET  /predicciones/{id}            una decision completa, con su entrada y sus variables
  GET  /umbrales  ·  POST /umbrales  umbrales vigentes e historial; cambiar umbrales con quien y por que (RF3, RNF10)
  GET  /ejemplos                     usuarios y dispositivos reales para los botones del front
"""
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Literal

import httpx
import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout
from pydantic import BaseModel, Field

from . import reglas

DB_URL = os.getenv("DB_URL", "postgresql://randuin:randuin_local@localhost:5432/randuin")
IA_URL = os.getenv("IA_URL", "http://gateway:8080/api/ia")
API_KEY = os.getenv("API_KEY", "randuin-local")
IA_TIMEOUT = float(os.getenv("IA_TIMEOUT_MS", "100")) / 1000       # RNF3: 100 ms

# Conexiones a la base: se abren al arrancar y se reutilizan (abrir una por peticion cuesta mas que la consulta).
# Antes de entregar una, el pool comprueba que siga viva: si la base se reinicio, no se usa una conexion rota.
pool = ConnectionPool(DB_URL, min_size=1, max_size=10, open=False, timeout=2, check=ConnectionPool.check_connection,
                      kwargs={"autocommit": True, "row_factory": dict_row, "connect_timeout": 2})
cliente_ia = httpx.Client(timeout=IA_TIMEOUT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open()          # no espera a la base: si no esta, las peticiones responden 503 hasta que vuelva
    yield


app = FastAPI(title="Randuin - API de Decision (microservicio de dominio)", lifespan=lifespan)


class Evaluacion(BaseModel):
    """Contrato de POST /evaluar: lo que manda el front (en produccion, el Core de Pagos)."""
    usuario_id: str = Field(..., min_length=1, max_length=32, examples=["KP303245"])
    dispositivo_id: str | None = Field(None, max_length=32)
    comercio_id: str | None = Field(None, max_length=32)
    monto: float = Field(..., gt=0, le=1_000_000_000)
    moneda: Literal["CLP", "USD"] = "CLP"
    canal: Literal["app", "web", "pos", "api"] = "app"
    pais_transaccion: str = Field("CL", min_length=2, max_length=2)
    fecha_hora: datetime | None = Field(None, description="vacio = ahora; sin zona horaria = hora de Chile")


class NuevosUmbrales(BaseModel):
    umbral_revisar: float = Field(..., gt=0, lt=1)
    umbral_bloquear: float = Field(..., gt=0, le=1)
    autorizado_por: str = Field(..., min_length=3, max_length=100)   # quien lo autoriza en Riesgo (RO5)
    motivo: str = Field(..., min_length=5, max_length=300)


def conectar():
    """Una conexion del pool. Si la base no responde en 2 s, el cliente recibe 503."""
    return pool.connection()


@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(PoolTimeout)
def base_caida(request, error):
    # Sin base no se puede registrar la decision (RF7): se responde 503 y el Core aplica su motor de reglas.
    return JSONResponse(status_code=503, content={"error": "la base de datos no responde"})


def uno(con, sql, parametros=()):
    return con.execute(sql, parametros).fetchone()


@app.get("/health")
def health():
    with conectar() as con:
        uno(con, "SELECT 1")
    return {"estado": "ok", "servicio": "dominio", "base": "ok"}


def pedir_al_modelo(variables: dict, transaccion_id: str) -> dict:
    """POST /predict pasando por el gateway, con la API key. Lanza excepcion si no responde a tiempo."""
    r = cliente_ia.post(f"{IA_URL}/predict", json={**variables, "transaccion_id": transaccion_id},
                        headers={"X-API-Key": API_KEY})
    r.raise_for_status()
    return r.json()


@app.post("/evaluar")
def evaluar(e: Evaluacion):
    inicio = time.perf_counter()
    transaccion_id = "TX-" + uuid.uuid4().hex[:10].upper()
    fecha = reglas.fecha_utc(e.fecha_hora)
    pais = e.pais_transaccion.upper()
    monto_usd = reglas.a_usd(e.monto, e.moneda)

    with conectar() as con:
        if not uno(con, "SELECT 1 FROM usuarios WHERE usuario_id = %s", (e.usuario_id,)):
            raise HTTPException(404, f"el usuario {e.usuario_id} no existe")
        umbral = uno(con, "SELECT * FROM umbrales ORDER BY version DESC LIMIT 1")
        en_lista = con.execute(
            """SELECT tipo, valor, motivo FROM lista_negra
               WHERE vigente AND ((tipo = 'dispositivo' AND valor = %s) OR (tipo = 'comercio' AND valor = %s))""",
            (e.dispositivo_id, e.comercio_id)).fetchall()
        intentos = uno(con, """SELECT count(*) AS n FROM predicciones
                               WHERE usuario_id = %s AND fecha_transaccion BETWEEN %s AND %s""",
                       (e.usuario_id, fecha - timedelta(hours=1), fecha))["n"]
        dispositivo = uno(con, "SELECT usuarios_distintos FROM dispositivos WHERE dispositivo_id = %s",
                          (e.dispositivo_id,))
        variables = reglas.variables_modelo(
            monto_usd=monto_usd, fecha=fecha, intentos_previos_1h=intentos, dispositivo_id=e.dispositivo_id,
            usuarios_en_dispositivo=dispositivo["usuarios_distintos"] if dispositivo else None,
            pais_transaccion=pais, canal=e.canal)

        score = modelo_version = replica = None
        if en_lista:                                   # 2. regla dura: el modelo no se consulta
            decision, origen = "bloquear", "lista_negra"
            razones = [{"regla": "lista_negra", "condicion": f"{f['tipo']} {f['valor']} esta en la lista negra ({f['motivo']})"}
                       for f in en_lista]
        else:                                          # 4. modelo, o politica de reserva si no responde
            try:
                p = pedir_al_modelo(variables, transaccion_id)
                score, modelo_version, replica, razones = p["score"], p["modelo_version"], p["replica"], p["razones"]
                decision = reglas.decidir(score, float(umbral["umbral_revisar"]), float(umbral["umbral_bloquear"]))
                origen = "modelo"
            except (httpx.HTTPError, KeyError, ValueError) as falla:   # caido, lento o respuesta rara
                decision, razones = reglas.reserva(variables, type(falla).__name__)
                origen = "reserva"

        latencia_ms = round((time.perf_counter() - inicio) * 1000, 2)
        con.execute(                                   # 5. registro completo (RF7)
            """INSERT INTO predicciones (transaccion_id, fecha_transaccion, usuario_id, dispositivo_id, comercio_id,
                   monto_usd, canal, entrada, variables, score, decision, origen, razones, modelo_version,
                   umbral_version, replica_ia, latencia_ms)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (transaccion_id, fecha, e.usuario_id, e.dispositivo_id, e.comercio_id, monto_usd, e.canal,
             Jsonb(e.model_dump(mode="json")), Jsonb(variables), score, decision, origen, Jsonb(razones),
             modelo_version, umbral["version"], replica, latencia_ms))

    return {"transaccion_id": transaccion_id, "decision": decision, "origen": origen, "score": score,
            "razones": razones, "modelo_version": modelo_version, "replica_ia": replica,
            "umbrales": {"version": umbral["version"], "revisar": float(umbral["umbral_revisar"]),
                         "bloquear": float(umbral["umbral_bloquear"])},
            "variables": variables, "latencia_ms": latencia_ms}


@app.get("/predicciones")
def predicciones(limite: int = 10):
    with conectar() as con:
        return con.execute(
            """SELECT transaccion_id, creado_en, usuario_id, monto_usd, canal, decision, origen, score,
                      modelo_version, replica_ia, latencia_ms
               FROM predicciones ORDER BY id DESC LIMIT %s""", (min(max(limite, 1), 100),)).fetchall()


@app.get("/predicciones/{transaccion_id}")
def prediccion(transaccion_id: str):
    with conectar() as con:
        fila = uno(con, "SELECT * FROM predicciones WHERE transaccion_id = %s", (transaccion_id,))
    if not fila:
        raise HTTPException(404, "no existe esa prediccion")
    return fila


@app.get("/umbrales")
def umbrales():
    with conectar() as con:
        historial = con.execute("SELECT * FROM umbrales ORDER BY version DESC").fetchall()
    return {"vigente": historial[0], "historial": historial}


@app.post("/umbrales", status_code=201)
def cambiar_umbrales(u: NuevosUmbrales):
    if u.umbral_revisar >= u.umbral_bloquear:
        raise HTTPException(422, "umbral_revisar tiene que ser menor que umbral_bloquear")
    with conectar() as con:
        return uno(con, """INSERT INTO umbrales (umbral_revisar, umbral_bloquear, autorizado_por, motivo)
                           VALUES (%s, %s, %s, %s) RETURNING *""",
                   (u.umbral_revisar, u.umbral_bloquear, u.autorizado_por, u.motivo))


@app.get("/ejemplos")
def ejemplos():
    """Datos reales de la base para los botones de ejemplo del front."""
    with conectar() as con:
        normal = uno(con, """SELECT t.usuario_id, t.dispositivo_id, t.comercio_id FROM transacciones t
                             JOIN dispositivos d USING (dispositivo_id)
                             WHERE t.es_fraude = 0 AND d.usuarios_distintos = 1 AND t.comercio_id IS NOT NULL
                             ORDER BY t.transaccion_id LIMIT 1""")
        compartido = uno(con, """SELECT dispositivo_id, usuarios_distintos FROM dispositivos
                                 WHERE usuarios_distintos >= 5 AND dispositivo_id NOT IN
                                       (SELECT valor FROM lista_negra WHERE tipo = 'dispositivo')
                                 ORDER BY usuarios_distintos DESC, dispositivo_id LIMIT 1""")
        bloqueado = uno(con, """SELECT valor AS dispositivo_id, motivo FROM lista_negra
                                WHERE vigente AND tipo = 'dispositivo' ORDER BY entrada_id LIMIT 1""")
    return {"normal": normal, "dispositivo_compartido": compartido, "lista_negra": bloqueado}
