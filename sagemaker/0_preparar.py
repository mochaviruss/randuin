"""Paso 0: de datos/salida/etiquetadas.csv (lo genera datos/preparar.py) a los archivos del Training Job.

    python3 datos/preparar.py            # una vez: crudos -> tratados (EP2)
    python3 sagemaker/0_preparar.py      # este paso

Hace la MISMA separacion por fecha de servicio-ia/entrenar.py (EP2), para que la red de SageMaker se mida con
las mismas filas:
    prueba        = conjunto fijo datos/conjunto_prueba.csv (el 25 % mas reciente de lo etiquetado, RF11).
                    NO se sube a S3: la red no la ve nunca. La usa 4_probar.py.
    validacion    = el 20 % mas reciente de lo que queda (se informa)
    entrenamiento = el resto
Cada transaccion queda como 10 numeros (vectorizar): el escalado y el log del monto los hace la red por dentro,
asi el entrenamiento y el servicio no pueden calcularlos distinto.

Salidas:
    sagemaker/datos/train.csv, validation.csv   sin encabezado: las 10 columnas y, al final, es_fraude
    sagemaker/datos/prueba.csv                  con encabezado e id de transaccion (para 4_probar.py)
    servicio-ia/app/referencia.json             valores tipicos (para las razones por oclusion, RF2) y medias
                                                del entrenamiento (para el drift, 8.1): lo lee el servicio de IA
Solo libreria estandar: corre en tu computador y en CloudShell.
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter

from comun_sm import COLUMNAS, DATOS, NUMERICAS, OBJETIVO, RAIZ, vectorizar

ETIQUETADAS = RAIZ / "datos" / "salida" / "etiquetadas.csv"
REFERENCIA = RAIZ / "servicio-ia" / "app" / "referencia.json"


def escribir(ruta, filas, encabezado=None):
    with open(ruta, "w", newline="") as f:
        w = csv.writer(f)
        if encabezado:
            w.writerow(encabezado)
        w.writerows(filas)


def main() -> int:
    if not ETIQUETADAS.exists():
        print(f"No existe {ETIQUETADAS}. Corre primero: python3 datos/preparar.py", file=sys.stderr)
        return 2
    with open(ETIQUETADAS, newline="") as f:
        filas = sorted(csv.DictReader(f), key=lambda r: r["ts_utc"])         # por fecha, como entrenar.py
    prueba = [r for r in filas if r["conjunto"] == "prueba"]
    resto = [r for r in filas if r["conjunto"] == "entrenamiento"]
    corte = int(len(resto) * 0.8)
    entrenamiento, validacion = resto[:corte], resto[corte:]

    def a_fila(r):
        return vectorizar(r) + [int(r[OBJETIVO])]

    DATOS.mkdir(exist_ok=True)
    escribir(DATOS / "train.csv", [a_fila(r) for r in entrenamiento])
    escribir(DATOS / "validation.csv", [a_fila(r) for r in validacion])
    escribir(DATOS / "prueba.csv", [[r["transaccion_id"]] + a_fila(r) for r in prueba],
             ["transaccion_id"] + COLUMNAS + [OBJETIVO])

    # --- referencia para el servicio de IA: solo con el entrenamiento, como el preprocesador de EP2 -------------------
    tipicos = {v: float(statistics.median(float(r[v]) for r in entrenamiento)) for v in NUMERICAS}
    tipicos["sin_dispositivo"] = float(Counter(int(r["sin_dispositivo"]) for r in entrenamiento).most_common(1)[0][0])
    tipicos["pais_extranjero"] = float(Counter(int(r["pais_extranjero"]) for r in entrenamiento).most_common(1)[0][0])
    tipicos["canal"] = Counter(r["canal"] for r in entrenamiento).most_common(1)[0][0]
    estadisticas = {v: {"media": round(statistics.fmean(float(r[v]) for r in entrenamiento), 3),
                        "desviacion": round(statistics.stdev(float(r[v]) for r in entrenamiento), 3)}
                    for v in ["monto_usd", "hora_local", "intentos_previos_1h"]}
    referencia = {"valores_tipicos": tipicos, "estadisticas_entrenamiento": estadisticas,
                  "conjuntos": {"entrenamiento": [len(entrenamiento), sum(int(r[OBJETIVO]) for r in entrenamiento)],
                                "validacion": [len(validacion), sum(int(r[OBJETIVO]) for r in validacion)],
                                "prueba_fija": [len(prueba), sum(int(r[OBJETIVO]) for r in prueba)]}}
    REFERENCIA.parent.mkdir(parents=True, exist_ok=True)
    REFERENCIA.write_text(json.dumps(referencia, indent=2, ensure_ascii=False))

    c = referencia["conjuntos"]
    print(f"entrenamiento {c['entrenamiento'][0]} ({c['entrenamiento'][1]} fraudes) | "
          f"validacion {c['validacion'][0]} ({c['validacion'][1]}) | prueba fija {c['prueba_fija'][0]} "
          f"({c['prueba_fija'][1]})")
    print(f"OK: {DATOS}/train.csv, validation.csv, prueba.csv y {REFERENCIA.relative_to(RAIZ)}")
    print("Siguiente: python3 sagemaker/1_subir.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
