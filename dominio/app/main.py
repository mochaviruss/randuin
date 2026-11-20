"""Microservicio de dominio de Randuin (la API de Decision de la EP1).

Es el UNICO servicio que lee y escribe la base. Para cada transaccion:
  1. Valida la entrada (Pydantic: si no cumple, 422) y que el usuario exista (si no, 404).
  2. Lista negra (RF4, RF8): si el dispositivo o el comercio estan en ella, bloquea sin consultar al modelo.
  3. Calcula las variables del modelo con datos de la base (velocidad RF10, dispositivo, hora, pais, canal).
  4. Pide la probabilidad de fraude al servicio de IA A TRAVES DEL GATEWAY (que reparte entre las 2 replicas),
     con un tope (IA_TIMEOUT_MS, 100 ms, RNF3). Si no responde, aplica la politica de reserva.
  5. Decide con los umbrales vigentes de Riesgo (RF3) y guarda la prediccion completa (RF7).
  6. EP3: si la decision es "revisar" o "bloquear", DESPUES de responder publica en la cola el pedido del resumen que redactara el LLM
     (RF5). El LLM nunca esta en el camino de la decision.

Rutas del enunciado de EP3 (2.5), publicadas por el gateway tal cual:
  GET  /api/health                  estado de cada pieza por separado: base, SageMaker, cola y LLM        (publica)
  POST /api/predict                 la decision del caso, con su version de modelo; guarda entrada y resultado  (X-API-Key)
  GET  /api/predicciones/{id}       la decision guardada y, si ya llego, el texto del LLM                  (X-API-Key)
  GET  /api/revision                la cola de revision priorizada por riesgo y monto, con el resumen       (X-API-Key)
  GET  /api/metricas                lo que muestra el dashboard                                             (X-API-Key)
  GET  /docs                        documentacion OpenAPI                                                   (publica)
Rutas de EP2 (siguen): /health, /evaluar, /predicciones, /predicciones/{id}, /umbrales, /ejemplos.
Rutas internas, que el gateway NO publica: GET y PUT /interno/predicciones/{id}/llm (las usa el worker).
"""
import hmac
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Literal

import httpx
import psycopg
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout
from pydantic import BaseModel, ConfigDict, Field

from . import cola, reglas, resumen

log = logging.getLogger("dominio")

DB_URL = os.getenv("DB_URL", "postgresql://randuin:randuin_local@localhost:5432/randuin")
IA_URL = os.getenv("IA_URL", "http://gateway:8080/api/ia")
# La clave NO tiene valor por defecto: sin ella ninguna ruta protegida acepta a nadie (falla cerrada).
API_KEY = os.getenv("API_KEY", "")
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


DESCRIPCION = """
**Randuin** decide cada transaccion de Kipu Pagos antes de autorizarla: *aprobar*, *revisar* o *bloquear*, con las razones que la
explican y la version del modelo que la produjo.

* El **modelo** (una red neuronal en un endpoint de Amazon SageMaker) decide. El **LLM** solo redacta, despues, un resumen para el
  analista, por una cola: si falla, la decision no cambia y el resumen queda *degradado*.
* Las rutas protegidas exigen la clave en el header `X-API-Key` (sin ella: **401**). Una entrada mal formada responde **422** con el detalle.
* Pasado el limite de peticiones, el gateway responde **429**.
"""

app = FastAPI(title="Randuin - API de Decision (microservicio de dominio)", description=DESCRIPCION, version="3.0.0", lifespan=lifespan,
              openapi_tags=[{"name": "Decision", "description": "La decision del caso y sus consultas (requieren X-API-Key)."},
                            {"name": "Operacion", "description": "Estado del sistema (publico) y metricas del dashboard."},
                            {"name": "EP2", "description": "Rutas de la version anterior, siguen funcionando."}])

cabecera_clave = APIKeyHeader(name="X-API-Key", auto_error=False, description="Clave de la API (la de la EC2, en su .env).")


def exigir_clave(clave: str | None = Security(cabecera_clave)) -> None:
    """401 si falta la clave o no coincide. Se compara en tiempo constante, y sin API_KEY configurada nadie entra."""
    if not API_KEY or not clave or not hmac.compare_digest(clave.encode(), API_KEY.encode()):
        raise HTTPException(401, "falta la API key (header X-API-Key) o no es valida")


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


class Transaccion(Evaluacion):
    """Contrato de POST /api/predict. Igual que el de EP2, pero estricto: un campo que no existe se rechaza con 422.
    Asi el numero de tarjeta (RNF5, RT4) no puede colarse: ni siquiera es un campo del contrato."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [
        {"usuario_id": "KP303245", "dispositivo_id": "D-5d47ee1b2e46", "comercio_id": "CM40413", "monto": 12000, "moneda": "CLP",
         "canal": "app", "pais_transaccion": "CL"}]})


class Llm(BaseModel):
    """El resumen en lenguaje comun para el analista. NO es parte de la decision: la decision ya esta tomada."""
    estado: Literal["no_aplica", "pendiente", "listo", "degradado"] = Field(
        description="no_aplica: se aprobo, no lleva resumen · pendiente: en la cola · listo: llego el texto · "
                    "degradado: el LLM no respondio o la cola no estaba (la decision no cambia)")
    texto: str | None = None
    motivo: str | None = Field(None, description="por que quedo degradado")
    latencia_ms: int | None = None


class Decision(BaseModel):
    transaccion_id: str
    decision: Literal["aprobar", "revisar", "bloquear"]
    origen: Literal["modelo", "lista_negra", "reserva"] = Field(
        description="modelo: lo decidio la red · lista_negra: regla dura, el modelo no se consulto · reserva: el modelo no respondio a tiempo")
    score: float | None = Field(None, description="probabilidad de fraude del modelo (0 a 1); vacio si no se consulto")
    razones: list[dict] = Field(description="los factores que explican la decision, de mayor a menor peso (RF2)")
    modelo_version: str | None = Field(None, description="version del modelo (la configuracion del endpoint de SageMaker)")
    replica_ia: str | None = None
    umbrales: dict
    variables: dict
    latencia_ms: float
    llm: Llm
    consulta: str = Field(description="donde consultar la decision guardada y, cuando llegue, el resumen del LLM")


class NuevosUmbrales(BaseModel):
    umbral_revisar: float = Field(..., gt=0, lt=1)
    umbral_bloquear: float = Field(..., gt=0, le=1)
    autorizado_por: str = Field(..., min_length=3, max_length=100)   # quien lo autoriza en Riesgo (RO5)
    motivo: str = Field(..., min_length=5, max_length=300)


class ResultadoLlm(BaseModel):
    """Lo que el worker le entrega al dominio por la ruta interna."""
    estado: Literal["listo", "degradado"]
    texto: str | None = Field(None, max_length=4000)
    motivo: str | None = Field(None, max_length=300)
    latencia_ms: int | None = Field(None, ge=0)


def conectar():
    """Una conexion del pool. Si la base no responde en 2 s, el cliente recibe 503."""
    return pool.connection()


@app.exception_handler(RequestValidationError)
def entrada_invalida(request, error):
    """422 con el detalle de QUE campo fallo y por que, pero SIN el valor recibido. Pydantic lo incluye por defecto en "input": si
    alguien manda un numero de tarjeta, la API se lo devolveria en el cuerpo del error (RNF5: la tarjeta no circula ni se registra)."""
    detalle = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in error.errors()]
    return JSONResponse(status_code=422, content={"detail": detalle})


@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(PoolTimeout)
def base_caida(request, error):
    # Sin base no se puede registrar la decision (RF7): se responde 503 y el Core aplica su motor de reglas.
    return JSONResponse(status_code=503, content={"error": "la base de datos no responde"})


def uno(con, sql, parametros=()):
    return con.execute(sql, parametros).fetchone()


@app.get("/health", tags=["EP2"])
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


def _evaluar(e: Evaluacion) -> tuple[dict, dict | None]:
    """La decision completa. Devuelve (respuesta, pedido_llm): el pedido es lo que hay que publicar en la cola DESPUES de responder,
    o None si la decision es "aprobar" (una compra aprobada no lleva resumen)."""
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

        llm_estado = "pendiente" if decision in ("revisar", "bloquear") else "no_aplica"
        latencia_ms = round((time.perf_counter() - inicio) * 1000, 2)
        con.execute(                                   # 5. registro completo (RF7)
            """INSERT INTO predicciones (transaccion_id, fecha_transaccion, usuario_id, dispositivo_id, comercio_id,
                   monto_usd, canal, entrada, variables, score, decision, origen, razones, modelo_version,
                   umbral_version, replica_ia, latencia_ms, llm_estado)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (transaccion_id, fecha, e.usuario_id, e.dispositivo_id, e.comercio_id, monto_usd, e.canal,
             Jsonb(e.model_dump(mode="json")), Jsonb(variables), score, decision, origen, Jsonb(razones),
             modelo_version, umbral["version"], replica, latencia_ms, llm_estado))

    respuesta = {"transaccion_id": transaccion_id, "decision": decision, "origen": origen, "score": score,
                 "razones": razones, "modelo_version": modelo_version, "replica_ia": replica,
                 "umbrales": {"version": umbral["version"], "revisar": float(umbral["umbral_revisar"]),
                              "bloquear": float(umbral["umbral_bloquear"])},
                 "variables": variables, "latencia_ms": latencia_ms, "llm": {"estado": llm_estado},
                 "consulta": f"/api/predicciones/{transaccion_id}"}
    pedido = None
    if llm_estado == "pendiente":
        pedido = resumen.armar_pedido(transaccion_id=transaccion_id, decision=decision, origen=origen, razones=razones,
                                      monto_usd=monto_usd, canal=e.canal, hora_local=variables["hora_local"])
    return respuesta, pedido


def _marcar_llm(transaccion_id: str, estado: str, texto: str | None = None, motivo: str | None = None,
                latencia_ms: int | None = None) -> bool:
    with conectar() as con:
        cur = con.execute(
            """UPDATE predicciones SET llm_estado = %s, llm_texto = %s, llm_motivo = %s, llm_latencia_ms = %s,
                      llm_actualizado_en = now() WHERE transaccion_id = %s""",
            (estado, texto, motivo, latencia_ms, transaccion_id))
        return cur.rowcount > 0


def encolar_resumen(pedido: dict) -> None:
    """Tarea en segundo plano: corre DESPUES de responder. Si RabbitMQ no acepta el pedido, el resumen queda 'degradado' y la
    prediccion, que ya se respondio, no se entera."""
    try:
        cola.publicar(pedido)
    except Exception as falla:
        log.warning("no se pudo encolar el resumen de %s: %s", pedido["id"], type(falla).__name__)
        try:
            _marcar_llm(pedido["id"], "degradado", motivo=f"cola no disponible ({type(falla).__name__})")
        except Exception:
            log.exception("tampoco se pudo marcar el resumen de %s como degradado", pedido["id"])


# ---------------------------------------------------------------------------------------------- rutas del enunciado de EP3
@app.post("/api/predict", response_model=Decision, tags=["Decision"], dependencies=[Depends(exigir_clave)],
          summary="La decision de una transaccion: aprobar, revisar o bloquear")
def predecir(t: Transaccion, tareas: BackgroundTasks):
    """Evalua la transaccion: reglas duras primero (lista negra), despues el modelo de SageMaker y los umbrales de Riesgo.
    Responde con la decision, sus razones y la **version del modelo**, y la guarda con su entrada y su resultado (RF7).
    Si la decision es *revisar* o *bloquear*, el resumen para el analista se pide al LLM **despues** de responder, por una cola:
    consultalo en `consulta` cuando este listo."""
    respuesta, pedido = _evaluar(t)
    if pedido:
        tareas.add_task(encolar_resumen, pedido)
    return respuesta


def _forma_prediccion(fila: dict) -> dict:
    llm = {"estado": fila.pop("llm_estado"), "texto": fila.pop("llm_texto"), "motivo": fila.pop("llm_motivo"),
           "latencia_ms": fila.pop("llm_latencia_ms")}
    fila.pop("llm_actualizado_en", None)
    return {**fila, "llm": llm}


@app.get("/api/predicciones/{transaccion_id}", tags=["Decision"], dependencies=[Depends(exigir_clave)],
         summary="Una decision guardada y, si ya llego, el resumen del LLM")
def consultar_prediccion(transaccion_id: str):
    """La decision tal como se guardo (entrada, variables, razones, version del modelo y del umbral) y el estado del resumen del LLM.
    Es lo que un auditor pide para reproducir una decision (RF7, RNF7)."""
    with conectar() as con:
        fila = uno(con, """SELECT transaccion_id, creado_en, usuario_id, monto_usd, canal, entrada, variables, score, decision,
                                  origen, razones, modelo_version, umbral_version, replica_ia, latencia_ms,
                                  llm_estado, llm_texto, llm_motivo, llm_latencia_ms, llm_actualizado_en
                           FROM predicciones WHERE transaccion_id = %s""", (transaccion_id,))
    if not fila:
        raise HTTPException(404, "no existe esa prediccion")
    return _forma_prediccion(fila)


@app.get("/api/revision", tags=["Decision"], dependencies=[Depends(exigir_clave)],
         summary="La cola de revision, priorizada por riesgo y monto")
def cola_de_revision(limite: int = Query(20, ge=1, le=100, description="cuantas transacciones devolver"),
                     decision: Literal["todas", "revisar", "bloquear"] = Query("todas"),
                     horas: int | None = Query(None, ge=1, le=24 * 30, description="solo las de las ultimas N horas")):
    """Lo que un analista tiene que mirar: las transacciones marcadas (*revisar* o *bloquear*), de mayor a menor **prioridad**.

    `prioridad = riesgo x monto en USD`. El riesgo es el score del modelo; si no hubo score, se usa 1,0 para una regla dura
    (lista negra), 0,8 para *bloquear* por reserva y 0,5 para *revisar* por reserva. Cada una trae el resumen del LLM
    cuando ya llego (`llm.estado = listo`) o el motivo si quedo degradado."""
    condicion, parametros = "decision IN ('revisar', 'bloquear')", []
    if decision != "todas":
        condicion, parametros = "decision = %s", [decision]
    if horas:
        condicion += " AND creado_en > now() - make_interval(hours => %s)"
        parametros.append(horas)
    with conectar() as con:
        filas = con.execute(
            f"""SELECT transaccion_id, creado_en, usuario_id, monto_usd, canal, decision, origen, score, razones, modelo_version,
                       llm_estado, llm_texto, llm_motivo, llm_latencia_ms,
                       round((coalesce(score, CASE WHEN origen = 'lista_negra' THEN 1.0 WHEN decision = 'bloquear' THEN 0.8
                                                  ELSE 0.5 END) * monto_usd)::numeric, 2) AS prioridad
                FROM predicciones WHERE {condicion} ORDER BY prioridad DESC, id DESC LIMIT %s""",
            [*parametros, limite]).fetchall()
    cola_ = []
    for f in filas:
        llm = {"estado": f.pop("llm_estado"), "texto": f.pop("llm_texto"), "motivo": f.pop("llm_motivo"),
               "latencia_ms": f.pop("llm_latencia_ms")}
        cola_.append({**f, "llm": llm})
    return {"criterio": "prioridad = riesgo x monto_usd, de mayor a menor", "total": len(cola_), "cola": cola_}


# ------------------------------------------------------------------------------------------------------------ salud por pieza
_cache: dict[str, tuple[float, Any]] = {}


def _con_cache(clave: str, segundos: float, f):
    """Evita que /api/health (publica) golpee a RabbitMQ y a ia en cada visita."""
    ahora = time.monotonic()
    if clave in _cache and ahora - _cache[clave][0] < segundos:
        return _cache[clave][1]
    _cache[clave] = (ahora, f())
    return _cache[clave][1]


def _pieza_base() -> dict:
    try:
        with conectar() as con:
            uno(con, "SELECT 1")
        return {"estado": "ok", "detalle": "PostgreSQL responde"}
    except Exception as e:
        return {"estado": "error", "detalle": f"la base no responde ({type(e).__name__})"}


def _pieza_sagemaker() -> dict:
    def consultar():
        try:
            r = httpx.get(f"{IA_URL}/health", timeout=2)
            cuerpo = r.json()
        except Exception as e:
            return {"estado": "error", "detalle": f"el servicio de IA no responde ({type(e).__name__})"}
        estado = cuerpo.get("sagemaker")
        return {"estado": "ok" if estado == "InService" else "error",
                "detalle": f"endpoint {cuerpo.get('endpoint')}: {estado}", "modelo_version": cuerpo.get("modelo_version")}
    return _con_cache("sagemaker", 5, consultar)


def _pieza_cola() -> dict:
    ok, detalle = _con_cache("cola", 5, cola.comprobar)
    return {"estado": "ok" if ok else "error", "detalle": detalle}


def _pieza_llm() -> dict:
    """No se llama al LLM para saber como esta: se mira que paso con los ultimos resumenes (15 min)."""
    try:
        with conectar() as con:
            filas = con.execute("""SELECT llm_estado, count(*) AS n FROM predicciones
                                   WHERE llm_actualizado_en > now() - interval '15 minutes' GROUP BY 1""").fetchall()
    except Exception as e:
        return {"estado": "error", "detalle": f"no se pudo leer la base ({type(e).__name__})"}
    n = {f["llm_estado"]: f["n"] for f in filas}
    listos, degradados = n.get("listo", 0), n.get("degradado", 0)
    if not listos and not degradados:
        return {"estado": "sin datos", "detalle": "ningun resumen en los ultimos 15 minutos"}
    estado = "ok" if listos and listos >= degradados else "degradado"
    return {"estado": estado, "detalle": f"ultimos 15 min: {listos} listos y {degradados} degradados"}


@app.get("/api/health", tags=["Operacion"], summary="Estado de cada pieza, por separado")
def salud():
    """Base de datos, endpoint de SageMaker, cola y LLM, cada uno por su lado (EP3, 2.5). Es publica.
    Que el LLM o la cola fallen **no** tumba la prediccion: `estado` pasa a *degradado* y el sistema sigue decidiendo.
    Responde 503 solo si la base esta caida, porque sin ella no se puede registrar ninguna decision (RF7)."""
    piezas = {"base": _pieza_base(), "sagemaker": _pieza_sagemaker(), "cola": _pieza_cola(), "llm": _pieza_llm()}
    if piezas["base"]["estado"] != "ok":
        global_ = "caido"
    elif all(p["estado"] in ("ok", "sin datos") for p in piezas.values()):
        global_ = "ok"
    else:
        global_ = "degradado"
    return JSONResponse(status_code=503 if global_ == "caido" else 200, content={"estado": global_, "piezas": piezas})


@app.get("/api/metricas", tags=["Operacion"], dependencies=[Depends(exigir_clave)], summary="Lo que muestra el dashboard")
def metricas(horas: int = Query(24, ge=1, le=24 * 30, description="ventana de tiempo, en horas")):
    """Decisiones evaluadas, su distribucion, que parte salio por la reserva, la latencia del dominio, la tasa de bloqueo por dia
    (RF12) y el estado de los resumenes del LLM."""
    ventana = timedelta(hours=horas)
    with conectar() as con:
        total = uno(con, """SELECT count(*) AS n,
                                   percentile_cont(0.50) WITHIN GROUP (ORDER BY latencia_ms) AS p50,
                                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latencia_ms) AS p95,
                                   percentile_cont(0.99) WITHIN GROUP (ORDER BY latencia_ms) AS p99
                            FROM predicciones WHERE creado_en > now() - %s""", (ventana,))
        por_decision = con.execute("""SELECT decision, count(*) AS n FROM predicciones WHERE creado_en > now() - %s GROUP BY 1""",
                                   (ventana,)).fetchall()
        por_origen = con.execute("""SELECT origen, count(*) AS n FROM predicciones WHERE creado_en > now() - %s GROUP BY 1""",
                                 (ventana,)).fetchall()
        por_llm = con.execute("""SELECT llm_estado, count(*) AS n FROM predicciones WHERE creado_en > now() - %s GROUP BY 1""",
                              (ventana,)).fetchall()
        por_dia = con.execute(
            """SELECT (creado_en AT TIME ZONE 'America/Santiago')::date AS dia, count(*) AS evaluadas,
                      count(*) FILTER (WHERE decision = 'bloquear') AS bloqueadas
               FROM predicciones WHERE creado_en > now() - interval '14 days' GROUP BY 1 ORDER BY 1""").fetchall()
        en_cola = uno(con, "SELECT count(*) AS n FROM predicciones WHERE decision IN ('revisar', 'bloquear') AND creado_en > now() - %s",
                      (ventana,))["n"]
    n_total = total["n"]
    origen = {f["origen"]: f["n"] for f in por_origen}
    redondear = lambda v: None if v is None else round(float(v), 1)  # noqa: E731
    return {
        "ventana_horas": horas,
        "evaluadas": n_total,
        "por_decision": {f["decision"]: f["n"] for f in por_decision},
        "por_origen": origen,
        "pct_reserva": round(100 * origen.get("reserva", 0) / n_total, 1) if n_total else 0.0,
        "latencia_ms": {"p50": redondear(total["p50"]), "p95": redondear(total["p95"]), "p99": redondear(total["p99"])},
        "bloqueo_por_dia": [{"dia": f["dia"].isoformat(), "evaluadas": f["evaluadas"], "bloqueadas": f["bloqueadas"],
                             "tasa_bloqueo_pct": round(100 * f["bloqueadas"] / f["evaluadas"], 1)} for f in por_dia],
        "en_cola_de_revision": en_cola,
        "resumenes_llm": {f["llm_estado"]: f["n"] for f in por_llm},
    }


# -------------------------------------------------------------------------------------------- rutas internas (las usa el worker)
@app.get("/interno/predicciones/{transaccion_id}/llm", include_in_schema=False)
def estado_llm(transaccion_id: str):
    with conectar() as con:
        fila = uno(con, "SELECT llm_estado FROM predicciones WHERE transaccion_id = %s", (transaccion_id,))
    if not fila:
        raise HTTPException(404, "no existe esa prediccion")
    return {"estado": fila["llm_estado"]}


@app.put("/interno/predicciones/{transaccion_id}/llm", include_in_schema=False)
def guardar_llm(transaccion_id: str, r: ResultadoLlm):
    if r.estado == "listo" and not (r.texto and r.texto.strip()):
        raise HTTPException(422, "un resumen 'listo' necesita texto")
    if not _marcar_llm(transaccion_id, r.estado, r.texto, r.motivo, r.latencia_ms):
        raise HTTPException(404, "no existe esa prediccion")
    return {"ok": True}


# ------------------------------------------------------------------------------------------------------------- rutas de EP2
@app.post("/evaluar", tags=["EP2"])
def evaluar(e: Evaluacion, tareas: BackgroundTasks):
    respuesta, pedido = _evaluar(e)
    if pedido:
        tareas.add_task(encolar_resumen, pedido)
    return respuesta


@app.get("/predicciones", tags=["EP2"])
def predicciones(limite: int = 10):
    with conectar() as con:
        return con.execute(
            """SELECT transaccion_id, creado_en, usuario_id, monto_usd, canal, decision, origen, score,
                      modelo_version, replica_ia, latencia_ms
               FROM predicciones ORDER BY id DESC LIMIT %s""", (min(max(limite, 1), 100),)).fetchall()


@app.get("/predicciones/{transaccion_id}", tags=["EP2"])
def prediccion(transaccion_id: str):
    with conectar() as con:
        fila = uno(con, "SELECT * FROM predicciones WHERE transaccion_id = %s", (transaccion_id,))
    if not fila:
        raise HTTPException(404, "no existe esa prediccion")
    return fila


@app.get("/umbrales", tags=["EP2"])
def umbrales():
    with conectar() as con:
        historial = con.execute("SELECT * FROM umbrales ORDER BY version DESC").fetchall()
    return {"vigente": historial[0], "historial": historial}


@app.post("/umbrales", status_code=201, tags=["EP2"])
def cambiar_umbrales(u: NuevosUmbrales):
    if u.umbral_revisar >= u.umbral_bloquear:
        raise HTTPException(422, "umbral_revisar tiene que ser menor que umbral_bloquear")
    with conectar() as con:
        return uno(con, """INSERT INTO umbrales (umbral_revisar, umbral_bloquear, autorizado_por, motivo)
                           VALUES (%s, %s, %s, %s) RETURNING *""",
                   (u.umbral_revisar, u.umbral_bloquear, u.autorizado_por, u.motivo))


@app.get("/ejemplos", tags=["EP2"])
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
