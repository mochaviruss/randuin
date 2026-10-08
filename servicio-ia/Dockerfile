# Imagen del Motor de Inferencia (servicio-ia). La construye el pipeline (.github/workflows/servicio-ia.yml)
# DESPUES de entrenar y pasar la compuerta: model/ ya tiene preprocesador.joblib, modelo.onnx y metadata.json.
# El modelo va DENTRO de la imagen (sin bind mounts). TensorFlow NO: se sirve con onnxruntime.
#
#   docker build -t servicio-ia:local .        (en local: antes python datos/preparar.py y python servicio-ia/entrenar.py)

# Imagen base oficial "slim": Debian minimo + Python. Sin compiladores ni CUDA.
FROM python:3.12-slim

# Commit del que sale la imagen (lo inyecta el pipeline con --build-arg). Se ve en la etiqueta y en /health.
ARG REVISION=local
LABEL org.opencontainers.image.title="servicio-ia" \
      org.opencontainers.image.description="Randuin: probabilidad de fraude y razones (TensorFlow -> ONNX)" \
      org.opencontainers.image.revision=$REVISION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GIT_SHA=$REVISION \
    MODEL_DIR=/app/model \
    ORT_THREADS=1

WORKDIR /app

# 1) Dependencias primero: esta capa se reutiliza mientras requirements.txt no cambie.
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2) Codigo y modelo despues: cambian seguido y se reconstruyen rapido.
COPY app/ ./app/
COPY model/ ./model/

# No corre como root.
RUN useradd --no-create-home --uid 10001 randuin
USER randuin

EXPOSE 8000

# Docker marca la replica healthy/unhealthy segun /health (503 si el modelo no cargo).
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
