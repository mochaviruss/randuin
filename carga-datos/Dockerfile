# Servicio de carga: crea las tablas, inserta los CSV tratados y termina.
# Los CSV van DENTRO de la imagen (sin bind mounts): los genera datos/preparar.py en el pipeline, antes del build.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY schema.sql cargar.py ./
COPY tratados/ tratados/
RUN useradd --no-create-home --uid 10001 randuin
USER randuin
CMD ["python", "cargar.py"]
