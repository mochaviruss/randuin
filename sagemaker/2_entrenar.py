"""Paso 2: lanza el Training Job con la red de EP2 (64-32) en el contenedor de TensorFlow de AWS.

    python3 sagemaker/2_entrenar.py
    python3 sagemaker/2_entrenar.py --capas 128,64 --epocas 80      # otra configuracion (DESAFIO de hiperparametros)

Modo script ("script mode"): SageMaker descarga el contenedor, baja de S3 el codigo (entrenar_sm.py empaquetado) y
los datos, corre el script y sube a S3 el model.tar.gz. Es reproducible: el modelo sale de ESTE script y de los
datos de 0_preparar.py, no de un archivo subido a mano (EP3, 2.2 regla 5).

Costo: ml.m5.large cobra solo los segundos que corre el job (unos 2 a 5 minutos).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tarfile
import time
from datetime import datetime

from comun_sm import (AQUI, CASO, IMAGEN_ENTRENAMIENTO, PREFIJO, REGION, bucket, guardar_estado, imagen_existe,
                      requiere, rol, s3, sm)

ESTADOS_FINALES = ("Completed", "Failed", "Stopped")


def empaquetar_codigo(nombre: str) -> str:
    """entrenar_sm.py dentro de un sourcedir.tar.gz en S3: asi lo espera el modo script de SageMaker."""
    memoria = io.BytesIO()
    with tarfile.open(fileobj=memoria, mode="w:gz") as tar:
        tar.add(str(AQUI / "entrenar_sm.py"), arcname="entrenar_sm.py")
    memoria.seek(0)
    clave = f"{CASO}/codigo/{nombre}/sourcedir.tar.gz"
    s3().upload_fileobj(memoria, bucket(), clave)
    return f"s3://{bucket()}/{clave}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--capas", default="64,32", help="neuronas por capa oculta (EP2: 64,32)")
    p.add_argument("--dropout", type=float, default=0.2)
    p.add_argument("--epocas", type=int, default=60)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--instancia", default="ml.m5.large")
    args = p.parse_args()
    s3_train, s3_validation = requiere("s3_train", "1_subir.py"), requiere("s3_validation", "1_subir.py")

    if not imagen_existe(IMAGEN_ENTRENAMIENTO):
        print(f"No encuentro la imagen {IMAGEN_ENTRENAMIENTO}.\n"
              "Revisa los tags en https://aws.github.io/deep-learning-containers/tensorflow/training/ y corre:\n"
              "  IMAGEN_ENTRENAMIENTO=<uri> python3 sagemaker/2_entrenar.py", file=sys.stderr)
        return 2

    nombre = f"{PREFIJO}{datetime.now():%Y%m%d-%H%M%S}"
    codigo = empaquetar_codigo(nombre)
    # En modo script, SageMaker espera cada valor de los hiperparametros codificado como JSON.
    hiper = {"sagemaker_program": "entrenar_sm.py", "sagemaker_submit_directory": codigo,
             "sagemaker_region": REGION, "sagemaker_job_name": nombre,
             "capas": args.capas, "dropout": args.dropout, "epocas": args.epocas, "lr": args.lr, "semilla": 42}
    hiper = {k: json.dumps(v) for k, v in hiper.items()}
    hiper["sagemaker_container_log_level"] = "20"

    def canal(nombre_canal, uri):
        return {"ChannelName": nombre_canal, "ContentType": "text/csv", "InputMode": "File",
                "DataSource": {"S3DataSource": {"S3DataType": "S3Prefix", "S3Uri": uri,
                                                "S3DataDistributionType": "FullyReplicated"}}}

    sm().create_training_job(
        TrainingJobName=nombre,
        AlgorithmSpecification={"TrainingImage": IMAGEN_ENTRENAMIENTO, "TrainingInputMode": "File",
                                "MetricDefinitions": [{"Name": "validation:aucpr",
                                                       "Regex": "validation_aucpr=([0-9\\.]+)"}]},
        RoleArn=rol(),
        InputDataConfig=[canal("train", s3_train), canal("validation", s3_validation)],
        OutputDataConfig={"S3OutputPath": f"s3://{bucket()}/{CASO}/modelos/"},
        ResourceConfig={"InstanceType": args.instancia, "InstanceCount": 1, "VolumeSizeInGB": 10},
        StoppingCondition={"MaxRuntimeInSeconds": 1800},      # tope de 30 min: un job colgado no se come el credito
        HyperParameters=hiper)
    print(f"Training Job {nombre} en {args.instancia} · red {args.capas} · {args.epocas} epocas. Se revisa cada 20 s.")

    inicio, anterior = time.time(), None
    while True:
        d = sm().describe_training_job(TrainingJobName=nombre)
        estado = f"{d['TrainingJobStatus']} · {d.get('SecondaryStatus', '')}"
        if estado != anterior:
            print(f"  {int(time.time() - inicio):>4} s · {estado}")
            anterior = estado
        if d["TrainingJobStatus"] in ESTADOS_FINALES:
            break
        time.sleep(20)

    if d["TrainingJobStatus"] != "Completed":
        print(f"El job termino en {d['TrainingJobStatus']}: {d.get('FailureReason', '(sin motivo)')}\n"
              f"Logs: CloudWatch -> Log groups -> /aws/sagemaker/TrainingJobs -> {nombre}", file=sys.stderr)
        return 1
    for m in d.get("FinalMetricDataList", []):
        print(f"  {m['MetricName']}: {m['Value']}")
    artefacto = d["ModelArtifacts"]["S3ModelArtifacts"]
    print(f"  Tiempo facturado: {d.get('BillableTimeInSeconds')} s")
    print(f"Modelo: {artefacto}")
    guardar_estado(entrenamiento=nombre, artefacto=artefacto, hiperparametros={k: json.loads(v) for k, v in hiper.items()
                                                                              if not k.startswith("sagemaker")})
    print("Siguiente: python3 sagemaker/3_endpoint.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
