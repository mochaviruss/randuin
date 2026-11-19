"""Paso 4: mide el endpoint (cold start y latencia) y la calidad del modelo sobre la PRUEBA FIJA.

    python3 sagemaker/4_probar.py                # cold start, 20 en caliente, metricas y compuerta
    python3 sagemaker/4_probar.py --solo-frio    # solo las dos primeras invocaciones (corrida tras 10 min sin uso)

Cold start medido DOS veces (EP3, apartado 3 del informe): una corrida justo despues de crear el endpoint o de 10 minutos
sin usarlo, y otra con el contenedor caliente. Las latencias de este script incluyen el viaje desde donde lo corras a
Virginia; la que importa para el servicio es ModelLatency en CloudWatch (desde la EC2, en la misma region).

Metricas (EP3, 3.3): precision, recall, F1 y PR-AUC con el umbral 0,30, mas la tasa de falsos positivos. La exactitud se
informa pero NO sirve para decidir: con 0,012 % de fraude en produccion, "nunca es fraude" acierta el 99,988 %.
Compuerta: los minimos de servicio-ia/entrenar.py (PR-AUC 0,80 y recall 0,70) y no empeorar contra servicio-ia/modelo_vigente.json
(tolerancia 0,025). Termina con codigo 1 si no pasa.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time

from comun_sm import DATOS, ENDPOINT, RAIZ, UMBRAL, guardar_estado, leer_estado, metricas, runtime

MINIMO_PR_AUC, MINIMO_RECALL, TOLERANCIA = 0.80, 0.70, 0.025


def invocar(filas: list) -> tuple:
    """Una invocacion con las filas dadas. Devuelve (probabilidades, milisegundos)."""
    t0 = time.perf_counter()
    r = runtime().invoke_endpoint(EndpointName=ENDPOINT, ContentType="application/json", Accept="application/json",
                                  Body=json.dumps({"instances": filas}))
    ms = (time.perf_counter() - t0) * 1000
    return parsear(r["Body"].read()), ms


def parsear(cuerpo) -> list:
    """TensorFlow Serving responde {"predictions": [[p], [p], ...]}: una probabilidad por fila."""
    pred = json.loads(cuerpo)["predictions"]
    return [float(p[0] if isinstance(p, list) else p) for p in pred]


def percentil(valores: list, q: float) -> float:
    orden = sorted(valores)
    return orden[min(len(orden) - 1, int(round(q * (len(orden) - 1))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo-frio", action="store_true")
    args = ap.parse_args()
    ruta = DATOS / "prueba.csv"
    if not ruta.exists():
        print(f"Falta {ruta}. Corre primero: python3 sagemaker/0_preparar.py", file=sys.stderr)
        return 2
    with open(ruta, newline="") as f:
        filas = list(csv.reader(f))[1:]
    X = [[float(v) for v in r[1:11]] for r in filas]                 # transaccion_id, 10 entradas, es_fraude
    y = [int(r[11]) for r in filas]

    print(f"### Cold start de {ENDPOINT} (dos invocaciones seguidas)")
    _, primera = invocar(X[:1])
    _, segunda = invocar(X[:1])
    print(f"  1.a invocacion: {primera:7.0f} ms")
    print(f"  2.a invocacion: {segunda:7.0f} ms")
    cold = {"primera_ms": round(primera), "segunda_ms": round(segunda)}
    if args.solo_frio:
        guardar_estado(cold_start=cold)
        print("Anota los dos numeros. Para la segunda medicion espera 10 minutos sin usar el endpoint y repite.")
        return 0

    tiempos = [invocar(X[i % len(X): i % len(X) + 1])[1] for i in range(20)]
    lat = {"p50": round(percentil(tiempos, .5)), "p95": round(percentil(tiempos, .95)),
           "p99": round(percentil(tiempos, 1.0)), "media": round(statistics.fmean(tiempos))}
    print(f"### 20 invocaciones en caliente: p50 {lat['p50']} ms · p95 {lat['p95']} ms · max {lat['p99']} ms")

    p, ms = invocar(X)
    m = metricas(y, p, UMBRAL)
    print(f"### Prueba fija: {len(y)} transacciones ({sum(y)} fraudes), umbral {UMBRAL}, una invocacion de {ms:.0f} ms")
    print(f"  VP {m['tp']} · FP {m['fp']} · FN {m['fn']} · VN {m['tn']}")
    print(f"  recall {m['recall_fraude']} · precision {m['precision_fraude']} · F1 {m['f1']} · PR-AUC {m['pr_auc']}")
    print(f"  tasa de falsos positivos {m['tasa_falsos_positivos']:.2%} · costo de errores USD {m['costo_errores_usd']}")
    print(f"  exactitud {m['exactitud']} (un modelo que nunca marca fraude saca {m['exactitud_nunca_fraude']}: no sirve para decidir)")

    vigente = json.loads((RAIZ / "servicio-ia" / "modelo_vigente.json").read_text(encoding="utf-8"))["metricas"]
    reglas = [("PR-AUC minima", m["pr_auc"], MINIMO_PR_AUC, m["pr_auc"] >= MINIMO_PR_AUC),
              ("recall minimo", m["recall_fraude"], MINIMO_RECALL, m["recall_fraude"] >= MINIMO_RECALL),
              ("no deja pasar mas fraude que el vigente", m["recall_fraude"], vigente["recall_fraude"],
               m["recall_fraude"] >= vigente["recall_fraude"] - TOLERANCIA),
              ("no genera mas falsos positivos que el vigente", m["tasa_falsos_positivos"],
               vigente["tasa_falsos_positivos"], m["tasa_falsos_positivos"] <= vigente["tasa_falsos_positivos"] + TOLERANCIA)]
    print("### Compuerta de calidad (la de EP2)")
    for nombre, valor, referencia, ok in reglas:
        print(f"  {'ok   ' if ok else 'FALLA'} {nombre}: candidato {valor} | referencia {referencia}")
    aprobado = all(r[3] for r in reglas)

    resultado = {"endpoint": ENDPOINT, "version": leer_estado().get("version"), "cold_start": cold,
                 "latencia_en_caliente_ms": lat, "metricas": m, "compuerta_aprobada": aprobado}
    (DATOS / "resultado_prueba.json").write_text(json.dumps(resultado, indent=2, ensure_ascii=False))
    guardar_estado(cold_start=cold, ultima_prueba=resultado)
    print(f"{'OK' if aprobado else 'NO PASA'}: guardado en {DATOS / 'resultado_prueba.json'}")
    return 0 if aprobado else 1


if __name__ == "__main__":
    sys.exit(main())
