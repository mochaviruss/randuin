"""Motor de Inferencia de Randuin (servicio-ia).

Hace una sola cosa: recibe las variables de una transaccion, aplica la red (entrenada con TensorFlow y
servida en ONNX con onnxruntime) y devuelve la probabilidad de fraude, las razones ordenadas por peso (RF2)
y la version del modelo. No toca la base (eso es de la API de Decision) y no decide aprobar/revisar/bloquear
(tambien es de la API de Decision, con los umbrales que fija Riesgo). No llama a ninguna API externa.

Endpoints:
    GET  /health        -> 200 si el modelo esta cargado, 503 si no (Docker y el gateway sacan la replica)
    GET  /modelo        -> metadata del modelo: version, framework, metricas con que paso la compuerta
    GET  /metrics       -> metricas para Prometheus (latencia de /predict, predicciones por clase)
    POST /predict       -> score, nivel, las 3 razones de mayor peso y la version del modelo
    POST /predict/lote  -> hasta 500 transacciones (sin razones, para reprocesos)

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
from prometheus_client import Counter, Histogram, make_asgi_app

from .esquemas import Lote, Prediccion, RespuestaLote, Transaccion
from .modelo import modelo

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("servicio-ia")

REVISION = os.getenv("GIT_SHA", "local")                   # commit del que salio la imagen (label revision)
REPLICA = os.getenv("REPLICA") or socket.gethostname()     # en Docker: id del contenedor, distingue las 2 replicas

# Metricas para el capitulo 8.1 (Prometheus las lee de cada replica).
LATENCIA = Histogram("randuin_ia_predict_segundos", "Latencia de /predict dentro del modelo",
                     buckets=(0.001, 0.002, 0.005, 0.01, 0.025, 0.05, 0.1))
PREDICCIONES = Counter("randuin_ia_predicciones_total", "Predicciones por clase", ["prediccion"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    # El modelo se carga UNA vez al arrancar, no en cada peticion (por eso /predict es rapido).
    try:
        modelo.cargar()
    except Exception:          # el servicio arranca igual, pero /health responde 503
        log.exception("No se pudo cargar el modelo")
    yield


app = FastAPI(title="Randuin - Motor de Inferencia (servicio-ia)",
              description="Probabilidad de fraude de una transaccion de Kipu Pagos, con sus razones (RF2).",
              version="2.0.0", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


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
    cuerpo = {"estado": "ok" if modelo.cargado else "degradado", "servicio": "ia",
              "modelo_cargado": modelo.cargado, "modelo_version": modelo.version,
              "modelo_hash": modelo.hash[:12], "framework": modelo.metadata.get("framework"),
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
    prediccion = "fraude" if score >= 0.5 else "legitima"
    segundos = time.perf_counter() - inicio
    LATENCIA.observe(segundos)
    PREDICCIONES.labels(prediccion).inc()
    return Prediccion(transaccion_id=t.transaccion_id, score=round(score, 4), prediccion=prediccion,
                      nivel_riesgo=modelo.nivel(score), umbral=modelo.umbral, razones=razones[:3], aportes=razones,
                      modelo_version=modelo.version, modelo_hash=modelo.hash, replica=REPLICA,
                      latencia_ms=round(segundos * 1000, 2))


@app.post("/predict/lote", response_model=RespuestaLote)
def predict_lote(lote: Lote):
    _exigir_modelo()
    inicio = time.perf_counter()
    scores = modelo.predecir([t.a_fila() for t in lote.transacciones])
    ms = round((time.perf_counter() - inicio) * 1000, 2)
    predicciones = [
        Prediccion(transaccion_id=t.transaccion_id, score=round(p, 4), prediccion="fraude" if p >= 0.5 else "legitima",
                   nivel_riesgo=modelo.nivel(p), umbral=modelo.umbral, razones=[], aportes=[],
                   modelo_version=modelo.version, modelo_hash=modelo.hash, replica=REPLICA, latencia_ms=ms)
        for t, p in zip(lote.transacciones, scores)]
    return RespuestaLote(modelo_version=modelo.version, total=len(predicciones),
                         alertas=sum(p.score >= modelo.umbral for p in predicciones), latencia_ms=ms,
                         predicciones=predicciones)
