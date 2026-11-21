"""El worker (EP3): saca un pedido de la cola, le pide el texto al LLM y se lo entrega al dominio.

    dominio --publica--> RabbitMQ (cola "llm") --> worker --HTTPS--> API de NVIDIA
                                                      |
                                                      +--PUT /interno/predicciones/{id}/llm--> dominio (guarda el texto)

Reglas que cumple (EP3, 2.3):
  - El LLM nunca esta en el camino de la prediccion: el dominio ya respondio cuando este mensaje se procesa.
  - Nunca decide: la decision la tomo el modelo de SageMaker. El LLM solo redacta.
  - No escribe en la base: le entrega el texto al dominio por su ruta interna, porque el dominio es el unico que escribe ahi.
  - Sobrevive al LLM: con clave invalida, 429 o la API caida, marca la prediccion "degradado" y sigue con el siguiente.

El mensaje es un JSON {"id": "<transaccion_id>", "instruccion": "...", "contenido": "..."}.
El dominio responde en GET /interno/predicciones/{id}/llm  {"estado": "pendiente|listo|degradado|no_aplica"} (404 si no existe)
y recibe en PUT el resultado: {"estado": "listo", "texto": "...", "latencia_ms": 640} o {"estado": "degradado", "motivo": "HTTP 401"}.

Cuidado con el limite de NVIDIA (~40 solicitudes por minuto por clave): el worker procesa de a un mensaje (prefetch 1) y deja
al menos LLM_MIN_INTERVALO_S entre una llamada y la siguiente (1,6 s = 37 por minuto). La cola absorbe las rafagas.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from app import llm
from app.llm import log

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/%2F")
URL_DOMINIO = os.getenv("URL_DOMINIO", "http://dominio:8000")
COLA, COLA_FALLIDOS = "llm", "llm.fallidos"
MIN_INTERVALO_S = float(os.getenv("LLM_MIN_INTERVALO_S", "1.6"))
LATIDO = Path(os.getenv("LATIDO", "/tmp/worker.vivo"))        # el healthcheck de Docker mira que este archivo se renueve


def entregar(id_: str, datos: dict, cliente=None) -> None:
    cliente = cliente or httpx
    r = cliente.put(f"{URL_DOMINIO}/interno/predicciones/{id_}/llm", json=datos, timeout=5)
    r.raise_for_status()


def estado_actual(id_: str, cliente=None) -> str | None:
    """El estado del texto de esa prediccion en el dominio, o None si la prediccion ya no existe."""
    cliente = cliente or httpx
    r = cliente.get(f"{URL_DOMINIO}/interno/predicciones/{id_}/llm", timeout=5)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json().get("estado")


def procesar(pedido: dict, redactar=llm.redactar, cliente=None) -> str:
    """Devuelve "listo", "degradado" o "descartado". Lanza httpx.HTTPError si el dominio no responde (el mensaje vuelve a la cola)."""
    estado = estado_actual(pedido["id"], cliente)
    if estado is None:
        return "descartado"        # la prediccion ya no existe: no se gasta una llamada ni se devuelve el mensaje a la cola
    if estado == "listo":
        return "listo"             # idempotencia: si el worker murio entre entregar y el ack, el mensaje llega dos veces
    inicio = time.perf_counter()
    try:
        texto = redactar(pedido["instruccion"], pedido["contenido"])
    except llm.LLMNoDisponible as e:
        entregar(pedido["id"], {"estado": "degradado", "motivo": str(e)}, cliente)
        return "degradado"
    entregar(pedido["id"], {"estado": "listo", "texto": texto,
                            "latencia_ms": round((time.perf_counter() - inicio) * 1000)}, cliente)
    return "listo"


class Ritmo:
    """Deja al menos MIN_INTERVALO_S entre dos llamadas al LLM (el limite por minuto de la clave gratuita)."""

    def __init__(self, intervalo: float = MIN_INTERVALO_S, reloj=time.monotonic, dormir=time.sleep):
        self.intervalo, self.reloj, self.dormir, self.ultima = intervalo, reloj, dormir, None

    def esperar(self) -> float:
        espera = 0.0
        if self.ultima is not None:
            espera = max(0.0, self.ultima + self.intervalo - self.reloj())
            if espera:
                self.dormir(espera)
        self.ultima = self.reloj()
        return espera


def latir() -> None:
    try:
        LATIDO.touch()
    except OSError:
        pass


def consumir() -> None:
    import pika
    parametros = pika.URLParameters(RABBITMQ_URL)
    # heartbeat largo: con los reintentos una llamada puede tardar mas de un minuto y mientras tanto el worker no atiende
    # la conexion. Con el heartbeat por defecto RabbitMQ lo daria por muerto.
    parametros.heartbeat = 600
    conexion = pika.BlockingConnection(parametros)
    canal = conexion.channel()
    canal.queue_declare(queue=COLA_FALLIDOS, durable=True)
    canal.queue_declare(queue=COLA, durable=True,
                        arguments={"x-dead-letter-exchange": "", "x-dead-letter-routing-key": COLA_FALLIDOS})
    canal.basic_qos(prefetch_count=1)          # de a un mensaje: el siguiente no llega hasta el ack
    ritmo = Ritmo()
    log(evento="inicio", modelo=llm.modelo_configurado() or "(falta NVIDIA_MODEL)", cola=COLA)
    # inactivity_timeout: el ciclo despierta cada 5 s aunque no lleguen mensajes, para renovar el latido del healthcheck.
    for metodo, _propiedades, cuerpo in canal.consume(COLA, inactivity_timeout=5):
        latir()
        if metodo is None:
            continue
        pedido = json.loads(cuerpo)
        try:
            ritmo.esperar()
            estado = procesar(pedido)
        except httpx.HTTPError as e:           # el dominio no respondio: el mensaje vuelve a la cola
            log(evento="llm", id=pedido.get("id"), estado="reintentar", motivo=type(e).__name__)
            time.sleep(2)
            canal.basic_nack(delivery_tag=metodo.delivery_tag, requeue=True)
            continue
        log(evento="llm", id=pedido["id"], estado=estado)
        if estado in ("listo", "descartado"):
            canal.basic_ack(delivery_tag=metodo.delivery_tag)
        else:
            # "degradado": ya se le aviso al dominio. requeue=False manda el mensaje a llm.fallidos en vez de devolverlo a la
            # cola (seria un bucle contra una clave mala). Ahi queda para reprocesarlo cuando se corrija la causa.
            canal.basic_nack(delivery_tag=metodo.delivery_tag, requeue=False)
        latir()


def main() -> None:
    import pika
    while True:                                # RabbitMQ puede arrancar despues que el worker, o reiniciarse
        try:
            consumir()
        except pika.exceptions.AMQPError as e:
            log(evento="conexion", estado="reconectando", motivo=type(e).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()
