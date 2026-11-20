# Imagen del worker del LLM (EP3). Consume la cola "llm" de RabbitMQ, llama a la API de NVIDIA y entrega el texto al dominio.
# La clave de NVIDIA NO va en la imagen: llega por variable de entorno desde el .env del servidor.
FROM python:3.12-slim

ARG REVISION=local
LABEL org.opencontainers.image.title="worker" \
      org.opencontainers.image.description="Randuin: redacta con un LLM el resumen para el analista, por la cola, sin bloquear la prediccion" \
      org.opencontainers.image.revision=$REVISION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/

# No corre como root.
RUN useradd --no-create-home --uid 10001 randuin
USER randuin

# El worker no tiene puerto: su healthcheck mira que renueve su latido (cada 5 s, aunque no lleguen mensajes).
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import os,sys,time; sys.exit(0 if time.time() - os.path.getmtime('/tmp/worker.vivo') < 60 else 1)"

CMD ["python", "-m", "app.worker"]
