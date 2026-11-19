"""Paso 3: publica el modelo en un endpoint serverless (o lo actualiza, sin cambiarle el nombre).

    python3 sagemaker/3_endpoint.py                                 # serverless, 2048 MB, concurrencia 10
    python3 sagemaker/3_endpoint.py --memoria 1024 --concurrencia 5
    python3 sagemaker/3_endpoint.py --tiempo-real                   # plan B: ml.m5.large, COBRA CADA HORA

El nombre del endpoint (randuin-fraude) no cambia nunca: el servicio de IA lo invoca por ese nombre y no se entera
de que el modelo cambio. La version que ve es el nombre de la configuracion nueva (randuin-fraude-<fecha>).

DECISION · memoria y concurrencia (es la pregunta del IE6; defiendelas con numeros medidos):
  - concurrencia 10: el pico de RNF4 es 92 tx/s (informe EP2, 1.000.000 tx/dia). Con una invocacion de unos 60 ms, por la
    ley de Little hay 92 x 0,06 = 5,5 invocaciones a la vez. Con 10 caben con margen. Con 2, la guia midio
    ThrottlingException y cerca de una de cada cuatro decisiones saliendo por la politica de reserva.
  - memoria 2048 MB: TensorFlow Serving necesita memoria para arrancar. Mide 1024 MB (cuesta la mitad por segundo) y
    compara el cold start y la latencia antes de cambiarla.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from botocore.exceptions import ClientError

from comun_sm import ENDPOINT, IMAGEN_INFERENCIA, PREFIJO, guardar_estado, imagen_existe, requiere, rol, sm

EN_TRANSITO = ("Creating", "Updating", "SystemUpdating", "RollingBack", "Deleting")


def estado_endpoint():
    try:
        return sm().describe_endpoint(EndpointName=ENDPOINT)["EndpointStatus"]
    except ClientError:
        return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--memoria", type=int, default=2048, help="MB del endpoint serverless (1024 a 6144, de a 1024)")
    p.add_argument("--concurrencia", type=int, default=10, help="invocaciones simultaneas maximas (serverless)")
    p.add_argument("--tiempo-real", action="store_true", help="plan B si el Learner Lab rechaza serverless")
    p.add_argument("--instancia", default="ml.m5.large", help="tipo de instancia del plan B (ml.t2.medium o ml.m5.large)")
    args = p.parse_args()
    artefacto = requiere("artefacto", "2_entrenar.py")

    if not imagen_existe(IMAGEN_INFERENCIA):
        print(f"No encuentro la imagen {IMAGEN_INFERENCIA}.\n"
              "Revisa los tags en https://aws.github.io/deep-learning-containers/tensorflow/inference/ y corre:\n"
              "  IMAGEN_INFERENCIA=<uri> python3 sagemaker/3_endpoint.py", file=sys.stderr)
        return 2

    estado = estado_endpoint()
    while estado in EN_TRANSITO:
        print(f"  El endpoint esta en {estado}: espero (cada 15 s).")
        time.sleep(15)
        estado = estado_endpoint()

    sello = f"{datetime.now():%Y%m%d-%H%M%S}"
    modelo = config = f"{PREFIJO}{sello}"
    sm().create_model(ModelName=modelo, ExecutionRoleArn=rol(),
                      PrimaryContainer={"Image": IMAGEN_INFERENCIA, "ModelDataUrl": artefacto})
    variante = {"VariantName": "principal", "ModelName": modelo}
    if args.tiempo_real:
        variante.update(InstanceType=args.instancia, InitialInstanceCount=1)
        tipo = f"tiempo real · {args.instancia} · COBRA CADA HORA: borralo con 5_borrar.py al terminar"
    else:
        variante["ServerlessConfig"] = {"MemorySizeInMB": args.memoria, "MaxConcurrency": args.concurrencia}
        tipo = f"serverless · {args.memoria} MB · concurrencia {args.concurrencia}"
    sm().create_endpoint_config(EndpointConfigName=config, ProductionVariants=[variante])

    if estado == "Failed":                                  # un endpoint fallido no se puede actualizar: se recrea
        sm().delete_endpoint(EndpointName=ENDPOINT)
        sm().get_waiter("endpoint_deleted").wait(EndpointName=ENDPOINT)
        estado = None
    if estado is None:
        sm().create_endpoint(EndpointName=ENDPOINT, EndpointConfigName=config)
        print(f"Creando {ENDPOINT} ({tipo})")
    else:
        sm().update_endpoint(EndpointName=ENDPOINT, EndpointConfigName=config)
        print(f"Actualizando {ENDPOINT} a la configuracion {config} ({tipo})")

    inicio, anterior = time.time(), None
    while True:
        d = sm().describe_endpoint(EndpointName=ENDPOINT)
        if d["EndpointStatus"] != anterior:
            print(f"  {int(time.time() - inicio):>4} s · {d['EndpointStatus']}")
            anterior = d["EndpointStatus"]
        if d["EndpointStatus"] in ("InService", "Failed"):
            break
        time.sleep(15)
    if d["EndpointStatus"] != "InService":
        print(f"El endpoint quedo en Failed: {d.get('FailureReason')}\n"
              "Si dice que no acepta serverless, usa el plan B: --tiempo-real (y documenta el error con una captura, "
              "EP3 2.2 regla 2).", file=sys.stderr)
        return 1
    print(f"Endpoint {ENDPOINT} InService · version {config}.")
    guardar_estado(endpoint=ENDPOINT, modelo=modelo, version=config, tipo=tipo)
    print("Siguiente: python3 sagemaker/4_probar.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
