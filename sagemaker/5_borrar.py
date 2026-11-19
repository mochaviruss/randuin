"""Paso 5: borra lo que cobra o estorba.

    python3 sagemaker/5_borrar.py           # endpoint, configuraciones y modelos (el modelo entrenado queda en S3)
    python3 sagemaker/5_borrar.py --todo    # ademas vacia y borra el bucket (hay que volver a correr desde el paso 1)

Un endpoint de tiempo real (plan B) cobra cada hora que esta encendido: borralo al terminar CADA sesion de trabajo.
El serverless solo cobra cuando se usa, pero tambien conviene borrarlo despues de la presentacion.
Como el modelo entrenado queda en S3, volver a crear el endpoint toma minutos con 3_endpoint.py, sin reentrenar.
"""
from __future__ import annotations

import argparse
import sys

from botocore.exceptions import ClientError

from comun_sm import ENDPOINT, PREFIJO, bucket, guardar_estado, s3, sm


def nombres(listar, clave_lista, clave_nombre):
    siguiente = None
    while True:
        r = listar(NameContains=PREFIJO, **({"NextToken": siguiente} if siguiente else {}))
        for x in r[clave_lista]:
            yield x[clave_nombre]
        siguiente = r.get("NextToken")
        if not siguiente:
            return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--todo", action="store_true", help="tambien borra el bucket de S3 y su contenido")
    args = ap.parse_args()

    try:
        sm().delete_endpoint(EndpointName=ENDPOINT)
        print(f"Borrando endpoint {ENDPOINT} ...")
        sm().get_waiter("endpoint_deleted").wait(EndpointName=ENDPOINT)
    except ClientError:
        print(f"No hay endpoint {ENDPOINT}.")
    for c in list(nombres(sm().list_endpoint_configs, "EndpointConfigs", "EndpointConfigName")):
        sm().delete_endpoint_config(EndpointConfigName=c)
        print(f"  configuracion borrada: {c}")
    for m in list(nombres(sm().list_models, "Models", "ModelName")):
        sm().delete_model(ModelName=m)
        print(f"  modelo borrado: {m}")
    guardar_estado(endpoint=None, modelo=None, version=None)

    if args.todo:
        b = bucket()
        try:
            for pagina in s3().get_paginator("list_objects_v2").paginate(Bucket=b):
                for o in pagina.get("Contents", []):
                    s3().delete_object(Bucket=b, Key=o["Key"])
            s3().delete_bucket(Bucket=b)
            guardar_estado(bucket=None, s3_train=None, s3_validation=None, artefacto=None, entrenamiento=None)
            print(f"Bucket {b} borrado.")
        except ClientError as e:
            print(f"No pude borrar el bucket: {e}", file=sys.stderr)
            return 1
    print("Listo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
