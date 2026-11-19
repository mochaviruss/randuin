# Imagen del microservicio de IA (servicio-ia). La construye el pipeline (.github/workflows/servicio-ia.yml).
# EP3: la imagen NO lleva el modelo. La red corre en un endpoint de SageMaker (carpeta sagemaker/) y este servicio la
# invoca con boto3, usando las credenciales del rol de la EC2. Lo unico que viaja en la imagen es app/referencia.json
# (valores tipicos del entrenamiento, para calcular las razones).
#
#   docker build -t servicio-ia:local .

# Imagen base oficial "slim": Debian minimo + Python. Sin compiladores ni CUDA.
FROM python:3.12-slim

# Commit del que sale la imagen (lo inyecta el pipeline con --build-arg). Se ve en la etiqueta y en /health.
ARG REVISION=local
LABEL org.opencontainers.image.title="servicio-ia" \
      org.opencontainers.image.description="Randuin: invoca el modelo de fraude en SageMaker y calcula las razones" \
      org.opencontainers.image.revision=$REVISION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GIT_SHA=$REVISION \
    AWS_DEFAULT_REGION=us-east-1 \
    ENDPOINT=randuin-fraude

WORKDIR /app

# 1) Dependencias primero: esta capa se reutiliza mientras requirements.txt no cambie.
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2) Codigo despues: cambia seguido y se reconstruye rapido.
COPY app/ ./app/

# No corre como root.
RUN useradd --no-create-home --uid 10001 randuin
USER randuin

EXPOSE 8000

# Docker marca la replica healthy/unhealthy segun /health: 200 si leyo su referencia. Que SageMaker este caido NO la
# marca unhealthy (no es falla de este contenedor): /predict responde 503 y el dominio aplica su politica de reserva.
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
