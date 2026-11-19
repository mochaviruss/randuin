"""Piezas comunes de los scripts de SageMaker (EP3). Solo libreria estandar y boto3: corre en CloudShell.

El orden de trabajo (cada paso anota lo que hizo en sagemaker/estado.json y se puede repetir):

    python3 sagemaker/0_preparar.py     datos/salida/etiquetadas.csv  ->  sagemaker/datos/*.csv  (+ referencia.json)
    python3 sagemaker/1_subir.py        train y validation a S3 (la prueba NO se sube)
    python3 sagemaker/2_entrenar.py     Training Job con la red de EP2 (64-32)
    python3 sagemaker/3_endpoint.py     endpoint serverless  (plan B: --tiempo-real)
    python3 sagemaker/4_probar.py       cold start, latencia y metricas sobre la prueba fija
    python3 sagemaker/5_borrar.py       borra endpoint, configuraciones y modelos

Autenticacion: el rol LabRole del Learner Lab. No hay access keys en ninguna parte (EP3, 2.2 regla 3 y 4).
"""
from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path

# boto3 se importa dentro de cada funcion: el paso 0 (preparar datos) corre en cualquier computador sin instalarlo.

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
DATOS = AQUI / "datos"                  # lo genera 0_preparar.py; no se sube al repositorio
ESTADO = AQUI / "estado.json"           # trae el ID de la cuenta: tampoco se sube (.gitignore)

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
CASO = "fraude"
ENDPOINT = os.getenv("ENDPOINT", "randuin-fraude")      # el nombre no cambia nunca: ia lo invoca por este nombre
PREFIJO = f"randuin-{CASO}-"                            # modelos y configuraciones: randuin-fraude-<sello>

# Imagenes de AWS Deep Learning Containers (CPU). Segun su documentacion, hoy solo hay entrenamiento 2.21 e
# inferencia (TensorFlow Serving) 2.20, ambas en Amazon Linux 2023 y Python 3.12.
# Se pueden cambiar sin tocar el codigo: IMAGEN_ENTRENAMIENTO=... IMAGEN_INFERENCIA=... python3 sagemaker/2_entrenar.py
REGISTRO_DLC = "763104351884"
_ECR = f"{REGISTRO_DLC}.dkr.ecr.{REGION}.amazonaws.com"
IMAGEN_ENTRENAMIENTO = os.getenv("IMAGEN_ENTRENAMIENTO", f"{_ECR}/tensorflow-training:2.21-cpu-py312-amzn2023-sagemaker")
IMAGEN_INFERENCIA = os.getenv("IMAGEN_INFERENCIA", f"{_ECR}/tensorflow-inference:2.20-cpu-py312-amzn2023-sagemaker")

# --- Las 7 variables de POST /predict, en el orden en que entran a la red --------------------------------------------
# Iguales a datos/preparar.py:VARIABLES. `canal` entra como 4 columnas 0/1 (app, web, pos, api).
NUMERICAS = ["monto_usd", "hora_local", "intentos_previos_1h", "dispositivo_usuarios_distintos",
             "sin_dispositivo", "pais_extranjero"]
CANALES = ["app", "web", "pos", "api"]
COLUMNAS = NUMERICAS + [f"canal_{c}" for c in CANALES]      # 10 numeros por transaccion
OBJETIVO = "es_fraude"

# Costos del caso (los mismos de servicio-ia/entrenar.py), para comparar en plata y no en exactitud.
UMBRAL = 0.30
COSTO_FRAUDE_USD = 57
COSTO_FALSO_POSITIVO_USD = round(1_162_600 / (460 * 365), 2)


def vectorizar(fila: dict) -> list:
    """Una transaccion (las 7 variables) -> los 10 numeros que recibe el endpoint. El escalado va DENTRO del modelo."""
    canal = str(fila["canal"])
    return [float(fila[v]) for v in NUMERICAS] + [1.0 if canal == c else 0.0 for c in CANALES]


# --- AWS ---------------------------------------------------------------------------------------------------------
def _cliente(servicio: str):
    import boto3
    return boto3.client(servicio, region_name=REGION)


@lru_cache(maxsize=None)
def sm():
    return _cliente("sagemaker")


@lru_cache(maxsize=None)
def s3():
    return _cliente("s3")


@lru_cache(maxsize=None)
def runtime():
    return _cliente("sagemaker-runtime")


@lru_cache(maxsize=None)
def cuenta() -> str:
    return _cliente("sts").get_caller_identity()["Account"]


def bucket() -> str:
    return f"randuin-ep3-{cuenta()}"           # los nombres de S3 son globales: la cuenta lo hace unico


def rol() -> str:
    return f"arn:aws:iam::{cuenta()}:role/LabRole"     # el Learner Lab no deja crear roles: se usa LabRole


def imagen_existe(uri: str) -> bool:
    """Comprueba que el tag exista en el ECR de AWS antes de gastar minutos de Training Job.
    Si el rol no tiene permiso para preguntar, no se bloquea nada: SageMaker lo dira al lanzar el job."""
    from botocore.exceptions import ClientError
    registro, resto = uri.split(".dkr.ecr.")[0], uri.split("/", 1)[1]
    repo, tag = resto.split(":")
    try:
        r = _cliente("ecr").batch_get_image(registryId=registro, repositoryName=repo, imageIds=[{"imageTag": tag}])
        return bool(r.get("images"))
    except ClientError:
        return True


# --- estado entre pasos --------------------------------------------------------------------------------------------
def leer_estado() -> dict:
    return json.loads(ESTADO.read_text()) if ESTADO.exists() else {}


def guardar_estado(**cambios) -> dict:
    e = leer_estado()
    e.update(cambios)
    ESTADO.write_text(json.dumps(e, indent=2, ensure_ascii=False))
    return e


def requiere(clave: str, paso: str):
    valor = leer_estado().get(clave)
    if not valor:
        sys.exit(f"Falta '{clave}'. Corre primero: python3 sagemaker/{paso}")
    return valor


# --- metricas (sin numpy ni scikit-learn: CloudShell solo trae boto3) -----------------------------------------------
def pr_auc(y: list, p: list) -> float:
    """Average precision: el area bajo la curva precision-recall, igual que sklearn.average_precision_score."""
    orden = sorted(range(len(y)), key=lambda i: -p[i])
    positivos = sum(y)
    if positivos == 0:
        return 0.0
    tp = fp = 0
    area, recall_previo = 0.0, 0.0
    i = 0
    while i < len(orden):
        j = i
        while j < len(orden) and p[orden[j]] == p[orden[i]]:      # los empates entran juntos
            tp += y[orden[j]]
            fp += 1 - y[orden[j]]
            j += 1
        recall = tp / positivos
        area += (recall - recall_previo) * (tp / (tp + fp))
        recall_previo = recall
        i = j
    return area


def metricas(y: list, p: list, umbral: float = UMBRAL) -> dict:
    pred = [1 if v >= umbral else 0 for v in p]
    tp = sum(1 for a, b in zip(y, pred) if a == 1 and b == 1)
    fp = sum(1 for a, b in zip(y, pred) if a == 0 and b == 1)
    fn = sum(1 for a, b in zip(y, pred) if a == 1 and b == 0)
    tn = len(y) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"umbral": umbral, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "recall_fraude": round(recall, 3), "precision_fraude": round(precision, 3),
            "f1": round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0.0,
            "tasa_falsos_positivos": round(fp / (fp + tn), 4) if fp + tn else 0.0,
            "pr_auc": round(pr_auc(y, p), 4),
            "exactitud": round((tp + tn) / len(y), 3),
            "exactitud_nunca_fraude": round(1 - sum(y) / len(y), 3),
            "costo_errores_usd": round(fn * COSTO_FRAUDE_USD + fp * COSTO_FALSO_POSITIVO_USD, 2)}
