"""Microservicio de IA de Randuin (Motor de Inferencia). Mismo patron que la guia del curso (clase-modelo-base-ia).

Recibe las variables de una transaccion, aplica la red (entrenada con TensorFlow y servida en ONNX con onnxruntime)
y devuelve la probabilidad de fraude, las 3 razones de mayor peso (RF2) y la version del modelo. No toca la base y no
decide aprobar/revisar/bloquear: eso lo hace el microservicio de dominio con los umbrales de Riesgo.

Endpoints (el gateway los publica bajo /api/ia; /predict exige la API key):
    GET  /health        200 si el modelo esta cargado, 503 si no
    GET  /modelo        metadata del modelo: version, framework, metricas con que paso la compuerta
    POST /predict       probabilidad, prediccion, 3 razones y version del modelo
    POST /predict/lote  hasta 500 transacciones (sin razones)

Correr en local (desde la carpeta servicio-ia, despues de entrenar):
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
from .modelo import modelo

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("servicio-ia")

REVISION = os.getenv("GIT_SHA", "local")                   # commit del que salio la imagen (IE10)
REPLICA = socket.gethostname()                             # en Docker es el id del contenedor: distingue las replicas


@asynccontextmanager
async def lifespan(app: FastAPI):
    # El modelo se carga UNA vez al arrancar, no en cada peticion (D8: la optimizacion que mas baja la latencia).
    try:
        modelo.cargar()
        modelo.probabilidades([modelo.metadata["valores_tipicos"]])     # una prediccion de calentamiento
    except Exception:          # el servicio arranca igual, pero /health responde 503 y Docker lo marca unhealthy
        log.exception("No se pudo cargar el modelo")
    yield


app = FastAPI(title="Randuin - Microservicio de IA",
              description="Probabilidad de fraude de una transaccion de Kipu Pagos, con sus razones (RF2).",
              version="2.0.0", lifespan=lifespan)


@app.middleware("http")
async def medir_tiempo(request: Request, call_next):
    t0 = time.perf_counter()
    respuesta = await call_next(request)
    respuesta.headers["X-Tiempo-ms"] = f"{(time.perf_counter() - t0) * 1000:.2f}"
    respuesta.headers["X-Version-Modelo"] = modelo.version or "sin-modelo"
    return respuesta


def _exigir_modelo() -> None:
    if not modelo.cargado:
        raise HTTPException(status_code=503, detail="Modelo no disponible")


@app.get("/health")
def health():
    cuerpo = {"estado": "ok" if modelo.cargado else "sin modelo", "servicio": "ia", "modelo_cargado": modelo.cargado,
              "modelo_version": modelo.version, "framework": modelo.metadata.get("framework"),
              "commit": REVISION, "replica": REPLICA}
    return JSONResponse(cuerpo, status_code=200 if modelo.cargado else 503)


@app.get("/modelo")
def info_modelo():
    _exigir_modelo()
    return modelo.metadata


@app.post("/predict", response_model=Prediccion)
def predict(t: Transaccion):
    _exigir_modelo()
    inicio = time.perf_counter()
    score, razones = modelo.predecir_con_razones(t.a_fila())
    return Prediccion(transaccion_id=t.transaccion_id, score=round(score, 4),
                      prediccion="fraude" if score >= 0.5 else "legitima", nivel_riesgo=modelo.nivel(score),
                      umbral=modelo.umbral, razones=razones[:3], modelo_version=modelo.version, replica=REPLICA,
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
                   umbral=modelo.umbral, razones=[], modelo_version=modelo.version, replica=REPLICA, latencia_ms=ms)
        for t, p in zip(lote.transacciones, scores)]
    return RespuestaLote(modelo_version=modelo.version, total=len(predicciones),
                         alertas=sum(p.score >= modelo.umbral for p in predicciones), latencia_ms=ms,
                         predicciones=predicciones)
