"""Paso 1: crea el bucket de S3 y sube los datos de entrenamiento y validacion.

    python3 sagemaker/1_subir.py

El bucket se llama randuin-ep3-<cuenta> (los nombres de S3 son globales; la cuenta lo hace unico).
La prueba fija (prueba.csv) NO se sube: la red no puede verla nunca, solo la usa 4_probar.py.
"""
from __future__ import annotations

import sys

from botocore.exceptions import ClientError

from comun_sm import CASO, DATOS, REGION, bucket, guardar_estado, s3


def main() -> int:
    for archivo in ("train.csv", "validation.csv"):
        if not (DATOS / archivo).exists():
            print(f"Falta {DATOS / archivo}. Corre primero: python3 sagemaker/0_preparar.py", file=sys.stderr)
            return 2
    b = bucket()
    try:
        s3().head_bucket(Bucket=b)
        print(f"Bucket existente: {b}")
    except ClientError:
        if REGION == "us-east-1":
            s3().create_bucket(Bucket=b)
        else:
            s3().create_bucket(Bucket=b, CreateBucketConfiguration={"LocationConstraint": REGION})
        print(f"Bucket creado: {b}")

    destinos = {}
    for canal, archivo in (("train", "train.csv"), ("validation", "validation.csv")):
        clave = f"{CASO}/{canal}/{archivo}"
        s3().upload_file(str(DATOS / archivo), b, clave)
        filas = sum(1 for _ in open(DATOS / archivo))
        print(f"  s3://{b}/{clave}  ·  {filas} filas")
        destinos[canal] = f"s3://{b}/{CASO}/{canal}/"
    guardar_estado(bucket=b, s3_train=destinos["train"], s3_validation=destinos["validation"])
    print("Siguiente: python3 sagemaker/2_entrenar.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
