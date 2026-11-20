"""La cola de RabbitMQ (EP3): el dominio publica aqui el pedido del resumen del LLM y el worker lo consume.

El LLM NUNCA esta en el camino de la prediccion: el dominio responde primero y publica DESPUES (ver main.encolar_resumen, que corre
como tarea en segundo plano, ya enviada la respuesta). Si RabbitMQ no esta, la prediccion responde igual y el resumen queda "degradado".

Las colas se declaran con los MISMOS argumentos que usa el worker (cola "llm" durable, con llm.fallidos como destino de lo que el
worker rechaza): si difirieran, RabbitMQ rechazaria la declaracion con PRECONDITION_FAILED.
pika se importa dentro de las funciones: los tests y el arranque no lo necesitan hasta publicar.
"""
from __future__ import annotations

import json
import os

COLA, COLA_FALLIDOS = "llm", "llm.fallidos"


def _url() -> str:
    return os.getenv("RABBITMQ_URL", "")


def _parametros():
    import pika
    parametros = pika.URLParameters(_url())
    parametros.socket_timeout = 2              # si RabbitMQ no contesta, se sabe en 2 s
    parametros.connection_attempts = 1
    parametros.retry_delay = 0
    parametros.blocked_connection_timeout = 2
    return parametros


def _declarar(canal) -> None:
    canal.queue_declare(queue=COLA_FALLIDOS, durable=True)
    canal.queue_declare(queue=COLA, durable=True,
                        arguments={"x-dead-letter-exchange": "", "x-dead-letter-routing-key": COLA_FALLIDOS})


def publicar(pedido: dict) -> None:
    """Publica {"id", "instruccion", "contenido"} en la cola "llm". Lanza una excepcion si RabbitMQ no lo acepta."""
    if not _url():
        raise RuntimeError("falta RABBITMQ_URL")
    import pika
    conexion = pika.BlockingConnection(_parametros())
    try:
        canal = conexion.channel()
        canal.confirm_delivery()               # el broker confirma que lo recibio: si no, lanza
        _declarar(canal)
        canal.basic_publish(exchange="", routing_key=COLA, body=json.dumps(pedido, ensure_ascii=False).encode(),
                            properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"))
    finally:
        conexion.close()


def comprobar() -> tuple[bool, str]:
    """(ok, detalle) para /api/health: abre y cierra una conexion."""
    if not _url():
        return False, "sin RABBITMQ_URL"
    try:
        import pika
        pika.BlockingConnection(_parametros()).close()
        return True, "RabbitMQ responde"
    except Exception as e:
        return False, f"RabbitMQ no responde ({type(e).__name__})"
