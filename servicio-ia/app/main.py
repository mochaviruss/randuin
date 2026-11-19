"""Microservicio de IA de Randuin (Motor de Inferencia).

Recibe las variables de una transaccion, invoca la red que vive en un endpoint de Amazon SageMaker (EP3) y devuelve la
probabilidad de fraude, las 3 razones de mayor peso (RF2) y la version del modelo. No toca la base y no decide
aprobar/revisar/bloquear: eso lo hace el microservicio de dominio con los umbrales de Riesgo.

Endpoints (el gateway los publica bajo /api/ia; /predict exige la API key):
    GET  /health        200 si el servicio esta sano; informa por separado el estado del endpoint de SageMaker
    GET  /modelo        metadata: version (configuracion del endpoint), framework, referencia del entrenamiento
    POST /predict       probabilidad, prediccion, 3 razones y version del modelo. 503 si SageMaker no responde
    POST /predict/lote  hasta 500 transacciones (sin razones)

Correr en local (desde la carpeta servicio-ia, con credenciales de AWS o dentro de la EC2 con su rol):
    uvicorn app.main:app --port 8000        y abrir http://localhost:8000/docs
"""
from __future__ import annotations

import logging
import os
import socket
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .esquemas import Lote, Prediccion, RespuestaLote, Transaccion
from .modelo import ErrorModelo, modelo

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("servicio-ia")

REVISION = os.getenv("GIT_SHA", "local")                   # commit del que salio la imagen (IE10)
REPLICA = socket.gethostname()                             # en Docker es el id del contenedor: distingue las replicas


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Se lee la referencia UNA vez al arrancar. No se invoca el endpoint aqui: un arranque no puede depender de que
    # SageMaker este caliente (con serverless, la primera invocacion tarda ~6 s).
    try:
        modelo.cargar()
    except Exception:          # sin referencia el servicio arranca igual, pero /health responde 503
        log.exception("No se pudo leer la referencia del modelo")
    yield


app = FastAPI(title="Randuin - Microservicio de IA",
              description="Probabilidad de fraude de una transaccion de Kipu Pagos, con sus razones (RF2). "
                          "El modelo corre en un endpoint de Amazon SageMaker.",
              version="3.0.0", lifespan=lifespan)


@app.middleware("http")
async def medir_tiempo(request: Request, call_next):
    t0 = time.perf_counter()
    respuesta = await call_next(request)
    respuesta.headers["X-Tiempo-ms"] = f"{(time.perf_counter() - t0) * 1000:.2f}"
    respuesta.headers["X-Version-Modelo"] = modelo.version or "sin-modelo"
    return respuesta


def _exigir_modelo() -> None:
    if not modelo.cargado:
        raise HTTPException(status_code=503, detail="Modelo no disponible: falta la referencia del entrenamiento")


def _version() -> str:
    return modelo.version or "desconocida"


@app.exception_handler(ErrorModelo)
def sagemaker_no_responde(request: Request, error: ErrorModelo):
    # 503 y no 500: el dominio lo trata como "el modelo no respondio" y aplica la politica de reserva (RNF3, ADR-6).
    log.warning("SageMaker no respondio (%s) en %s", error, request.url.path)
    return JSONResponse(status_code=503, content={"detail": "El endpoint de SageMaker no responde", "causa": str(error)})


@app.get("/health")
def health():
    """200 si el servicio esta sano (referencia leida). El estado del endpoint va aparte: si SageMaker se cae, este
    servicio NO se reinicia (no es su falla); /predict responde 503 y el dominio usa la reserva."""
    cuerpo = {"estado": "ok" if modelo.cargado else "sin referencia", "servicio": "ia", "modelo_cargado": modelo.cargado,
              "modelo_version": modelo.version, "framework": modelo.metadata.get("framework"),
              "motor": "sagemaker", "endpoint": modelo.metadata.get("endpoint"), "sagemaker": modelo.estado_endpoint,
              "commit": REVISION, "replica": REPLICA}
    return JSONResponse(cuerpo, status_code=200 if modelo.cargado else 503)


@app.get("/modelo")
def info_modelo():
    _exigir_modelo()
    return {**modelo.metadata, "version": modelo.version, "estado_endpoint": modelo.estado_endpoint}


@app.post("/predict", response_model=Prediccion)
def predict(t: Transaccion):
    _exigir_modelo()
    inicio = time.perf_counter()
    score, razones = modelo.predecir_con_razones(t.a_fila())
    return Prediccion(transaccion_id=t.transaccion_id, score=round(score, 4),
                      prediccion="fraude" if score >= 0.5 else "legitima", nivel_riesgo=modelo.nivel(score),
                      umbral=modelo.umbral, razones=razones[:3], modelo_version=_version(), replica=REPLICA,
                      latencia_ms=round((time.perf_counter() - inicio) * 1000, 2))


@app.post("/predict/lote", response_model=RespuestaLote)
def predict_lote(lote: Lote):
    _exigir_modelo()
    inicio = time.perf_counter()
    scores = modelo.probabilidades([t.a_fila() for t in lote.transacciones])
    ms = round((time.perf_counter() - inicio) * 1000, 2)
    predicciones = [
        Prediccion(transaccion_id=t.transaccion_id, score=round(float(p), 4),
                   prediccion="fraude" if p >= 0.5 else "legitima", nivel_riesgo=modelo.nivel(p),
                   umbral=modelo.umbral, razones=[], modelo_version=_version(), replica=REPLICA, latencia_ms=ms)
        for t, p in zip(lote.transacciones, scores)]
    return RespuestaLote(modelo_version=_version(), total=len(predicciones),
                         alertas=sum(p.score >= modelo.umbral for p in predicciones), latencia_ms=ms,
                         predicciones=predicciones)
