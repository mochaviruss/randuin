"""
Entrenamiento + compuerta de calidad del Motor de Inferencia de Randuin (servicio-ia).

Es la version "script" del notebook de experimentos (randuin_modelo_tensorflow.ipynb) y sigue el patron
de la guia del curso (clase-modelo-base-ia). Lo corre el workflow .github/workflows/servicio-ia.yml:

    1. lee datos/salida/etiquetadas.csv (lo genera datos/preparar.py)
    2. separa: prueba = el conjunto FIJO de datos/conjunto_prueba.csv (RF11, el 25 % mas reciente);
       validacion = el 20 % mas reciente de lo que queda (se informa); entrenamiento = el resto. Todo por fecha.
    3. ajusta el preprocesador (scikit-learn) SOLO con entrenamiento
    4. entrena una red MLP con TensorFlow / Keras (CPU, sin CUDA). Configuracion por defecto = el modelo 4 del
       notebook de Max (64-32, dropout 0,2, sin peso de clase, 60 epocas, lotes de 64), elegido con validacion
       cruzada por fecha entre los 7 modelos del notebook (docs/ELECCION-MODELO.md)
    5. compuerta: PR-AUC minima, recall minimo, AUC minimo y no empeorar contra el modelo vigente
       (modelo_vigente.json). Si no pasa -> codigo 1: no se exporta nada y el pipeline no construye la imagen
    6. exporta a servicio-ia/model/: preprocesador.joblib + modelo.onnx + metadata.json, y verifica que
       onnxruntime de lo mismo que TensorFlow

TensorFlow se instala en el job de CI o en tu computador, NUNCA en la imagen: la imagen solo lleva
onnxruntime (requirements.txt). Asi pesa ~550 MB en vez de ~2 GB.

Uso (desde la raiz del repositorio):
    pip install -r servicio-ia/requirements-tensorflow.txt
    python datos/preparar.py
    python servicio-ia/entrenar.py
    python servicio-ia/entrenar.py --capas 128,64 --version 1.1.0-prueba
    python servicio-ia/entrenar.py --pr-auc-minimo 0.99          # para ver fallar la compuerta
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
DATOS = RAIZ / "datos" / "salida" / "etiquetadas.csv"
REPORTE_LIMPIEZA = RAIZ / "datos" / "salida" / "reporte.json"
VIGENTE = AQUI / "modelo_vigente.json"
SALIDA = AQUI / "model"

SEMILLA = 42
# Las 7 entradas de POST /predict, iguales a datos/preparar.py:VARIABLES y a dominio/app/logica.py.
VARIABLES_NUMERICAS = ["monto_usd", "hora_local", "intentos_previos_1h", "dispositivo_usuarios_distintos",
                       "sin_dispositivo", "pais_extranjero"]
VARIABLES_CATEGORICAS = ["canal"]
OBJETIVO = "es_fraude"

# Las metricas se miden con el umbral "revisar" inicial de Riesgo (0,30). Riesgo lo cambia en el dominio
# (tabla umbrales, RF3) sin reconstruir esta imagen.
UMBRAL = 0.30
MINIMO_RECALL = 0.70   # de cada 10 fraudes del conjunto de prueba, detecta al menos 7
MINIMO_AUC = 0.80      # capacidad de ordenar fraude sobre no fraude
TOLERANCIA = 0.025     # cuanto puede empeorar contra el vigente (con 45 fraudes en prueba, 1 fraude = 0,022)

# Costos del caso (seccion 2), para comparar modelos en plata y no en exactitud.
COSTO_FRAUDE_USD = 57                                         # monto promedio de un fraude confirmado
COSTO_FALSO_POSITIVO_USD = round(1_162_600 / (460 * 365), 2)  # costo anual de bloquear buenos / bloqueos al ano


def construir_preprocesador() -> ColumnTransformer:
    """Monto en escala logaritmica (va de USD 1 a miles), el resto estandarizado, canal en one-hot."""
    monto = Pipeline([("imputar", SimpleImputer(strategy="median")), ("log", FunctionTransformer(np.log1p)),
                      ("escalar", StandardScaler())])
    resto = Pipeline([("imputar", SimpleImputer(strategy="median")), ("escalar", StandardScaler())])
    return ColumnTransformer([
        ("monto", monto, VARIABLES_NUMERICAS[:1]),
        ("num", resto, VARIABLES_NUMERICAS[1:]),
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), VARIABLES_CATEGORICAS),
    ])


def medir(y, prob) -> dict:
    y, prob = np.asarray(y), np.asarray(prob)
    pred = (prob >= UMBRAL).astype(int)
    falsos_positivos = int(((pred == 1) & (y == 0)).sum())
    no_detectados = int(((pred == 0) & (y == 1)).sum())
    return {"recall_fraude": round(float(recall_score(y, pred)), 3),
            "precision_fraude": round(float(precision_score(y, pred, zero_division=0)), 3),
            "tasa_falsos_positivos": round(falsos_positivos / int((y == 0).sum()), 4),
            "roc_auc": round(float(roc_auc_score(y, prob)), 4),
            "pr_auc": round(float(average_precision_score(y, prob)), 4),
            "fraude_no_detectado": no_detectados,
            "falsos_positivos": falsos_positivos,
            "costo_errores_usd": round(no_detectados * COSTO_FRAUDE_USD + falsos_positivos * COSTO_FALSO_POSITIVO_USD, 2)}


def entrenar_tensorflow(A_tr, y_tr, capas, dropout, epocas, lr, peso_clase):
    os.environ.setdefault("KERAS_BACKEND", "tensorflow")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")         # sin los avisos internos de TensorFlow
    import keras
    import tensorflow as tf

    keras.utils.set_random_seed(SEMILLA)
    tf.config.experimental.enable_op_determinism()      # misma red en cada ejecucion del pipeline
    red = keras.Sequential([keras.Input(shape=(A_tr.shape[1],), name="x")])
    for n in capas:
        red.add(keras.layers.Dense(n, activation="relu"))
        red.add(keras.layers.Dropout(dropout))
    red.add(keras.layers.Dense(1, activation="sigmoid"))          # salida: probabilidad de fraude
    red.compile(optimizer=keras.optimizers.Adam(lr), loss="binary_crossentropy")
    # Sin peso de clase: pesar los fraudes empuja a dar mas alertas y Riesgo pide bajar los falsos positivos.
    # Epocas fijas: la parada temprana, con tan pocos fraudes en validacion, fue inestable (docs/ELECCION-MODELO.md).
    pesos = {0: 1.0, 1: float((y_tr == 0).sum() / (y_tr == 1).sum())} if peso_clase else None
    red.fit(A_tr, y_tr, epochs=epocas, batch_size=64, class_weight=pesos, verbose=0)

    def predecir(A):
        return red.predict(A, verbose=0).ravel()

    def exportar(ruta: Path):
        red.export(str(ruta), format="onnx", verbose=False)        # usa tf2onnx

    return predecir, exportar, f"tensorflow {tf.__version__}"


def compuerta(m: dict, pr_auc_minimo: float) -> dict:
    """Paso 4 del pipeline de la EP1 (candidato contra campeon), con los minimos del equipo."""
    reglas = [("PR-AUC minima", m["pr_auc"], pr_auc_minimo, m["pr_auc"] >= pr_auc_minimo),
              ("recall minimo", m["recall_fraude"], MINIMO_RECALL, m["recall_fraude"] >= MINIMO_RECALL),
              ("AUC minimo", m["roc_auc"], MINIMO_AUC, m["roc_auc"] >= MINIMO_AUC)]
    vigente = json.loads(VIGENTE.read_text(encoding="utf-8")) if VIGENTE.exists() else None
    if vigente:
        v = vigente["metricas"]
        reglas += [("no deja pasar mas fraude que el vigente (recall)", m["recall_fraude"], v["recall_fraude"],
                    m["recall_fraude"] >= v["recall_fraude"] - TOLERANCIA),
                   ("no genera mas falsos positivos que el vigente", m["tasa_falsos_positivos"],
                    v["tasa_falsos_positivos"], m["tasa_falsos_positivos"] <= v["tasa_falsos_positivos"] + TOLERANCIA)]
    comparaciones = [{"regla": r, "candidato": c, "referencia": ref, "ok": bool(ok)} for r, c, ref, ok in reglas]
    return {"vigente": vigente["version"] if vigente else "(no hay vigente: solo minimos)",
            "comparaciones": comparaciones, "aprobado": all(c["ok"] for c in comparaciones)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Entrena la red de Randuin, aplica la compuerta y exporta a ONNX")
    ap.add_argument("--capas", default="64,32", help="neuronas por capa oculta, ej. 128,64")
    ap.add_argument("--dropout", type=float, default=0.2)
    ap.add_argument("--epocas", type=int, default=60)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--peso-clase", action="store_true",
                    help="pesar mas los fraudes (da mas alertas: por defecto no)")
    ap.add_argument("--version", default=None, help="ej. 1.0.37-tf (el pipeline usa el numero de ejecucion)")
    ap.add_argument("--pr-auc-minimo", type=float, default=0.80,
                    help="compuerta: si la PR-AUC en prueba es menor, termina con codigo 1")
    args = ap.parse_args()
    capas = [int(n) for n in args.capas.split(",") if n.strip()]
    commit = os.environ.get("GITHUB_SHA", "local")[:7]
    version = args.version or f"1.0.0-tf-{commit}"

    if not DATOS.exists():
        print(f"No existe {DATOS}. Corre primero: python datos/preparar.py", file=sys.stderr)
        return 2

    # ---- Datos: tres conjuntos que no se mezclan, separados por fecha ----------------------------
    df = pd.read_csv(DATOS).sort_values("ts_utc")
    columnas = VARIABLES_NUMERICAS + VARIABLES_CATEGORICAS
    prueba = df[df["conjunto"] == "prueba"]
    resto = df[df["conjunto"] == "entrenamiento"]
    corte = int(len(resto) * 0.8)
    entrenamiento, validacion = resto.iloc[:corte], resto.iloc[corte:]
    X_tr, X_val, X_te = entrenamiento[columnas], validacion[columnas], prueba[columnas]
    y_tr, y_val, y_te = (d[OBJETIVO].to_numpy() for d in (entrenamiento, validacion, prueba))

    prep = construir_preprocesador().fit(X_tr)          # se ajusta SOLO con entrenamiento
    A_tr, A_val, A_te = (prep.transform(X).astype(np.float32) for X in (X_tr, X_val, X_te))

    # ---- Linea base: regresion logistica con el mismo preprocesador --------------------------------
    base = LogisticRegression(max_iter=1000).fit(A_tr, y_tr)
    linea_base = medir(y_te, base.predict_proba(A_te)[:, 1])

    # ---- Red --------------------------------------------------------------------------------------
    predecir, exportar_onnx, motor = entrenar_tensorflow(
        A_tr, y_tr, capas, args.dropout, args.epocas, args.lr, args.peso_clase)
    p_te = predecir(A_te)
    metricas = medir(y_te, p_te)
    pr_auc_validacion = round(float(average_precision_score(y_val, predecir(A_val))), 4)
    descripcion = (f"MLP {capas} dropout={args.dropout} lr={args.lr} "
                   f"{'con' if args.peso_clase else 'sin'} peso de clase, {args.epocas} epocas")
    print(f"[{motor}] {descripcion} | PR-AUC validacion {pr_auc_validacion}")
    print(f"  red          (prueba fija, umbral {UMBRAL}): {json.dumps(metricas)}")
    print(f"  linea base   regresion logistica:           {json.dumps(linea_base)}")

    # ---- Compuerta ----------------------------------------------------------------------------------
    control = compuerta(metricas, args.pr_auc_minimo)
    for c in control["comparaciones"]:
        print(f"  {'ok   ' if c['ok'] else 'FALLA'} {c['regla']}: candidato {c['candidato']} | referencia {c['referencia']}")
    for viejo in ["modelo.onnx", "preprocesador.joblib", "metadata.json"]:
        (SALIDA / viejo).unlink(missing_ok=True)       # un candidato rechazado no deja nada para construir
    if not control["aprobado"]:
        print("ERROR: el candidato queda bajo los minimos o es peor que el vigente. "
              "No se exporta y el pipeline no construye la imagen.", file=sys.stderr)
        return 1
    print(f"OK: el candidato pasa la compuerta (vigente: {control['vigente']})")

    # ---- Exportar y verificar --------------------------------------------------------------------------
    SALIDA.mkdir(parents=True, exist_ok=True)
    joblib.dump(prep, SALIDA / "preprocesador.joblib")
    exportar_onnx(SALIDA / "modelo.onnx")
    import onnxruntime as ort
    sesion = ort.InferenceSession(str(SALIDA / "modelo.onnx"), providers=["CPUExecutionProvider"])
    p_onnx = sesion.run(None, {sesion.get_inputs()[0].name: A_te})[0].ravel()
    diferencia = float(np.abs(p_onnx - p_te).max())
    print(f"OK: ONNX verificado con onnxruntime, diferencia maxima {diferencia:.1e}")
    if diferencia > 1e-4:
        print("ERROR: el ONNX no reproduce a la red de TensorFlow", file=sys.stderr)
        for viejo in SALIDA.glob("*.*"):
            viejo.unlink()
        return 1

    # Valor "tipico" de cada variable: contra el se calculan las razones de cada prediccion (RF2).
    referencia = {v: float(X_tr[v].median()) for v in VARIABLES_NUMERICAS}
    referencia.update({v: str(X_tr[v].mode()[0]) for v in VARIABLES_CATEGORICAS})
    referencia.update({v: float(X_tr[v].mode()[0]) for v in ["sin_dispositivo", "pais_extranjero"]})

    metadata = {
        "nombre": "randuin-fraude",
        "version": version,
        "formato": "onnx",
        "framework": motor,
        "descripcion": descripcion,
        "entrenado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": os.environ.get("GITHUB_SHA", "local"),
        "sklearn_version": sklearn.__version__,
        "variables_numericas": VARIABLES_NUMERICAS,
        "variables_categoricas": VARIABLES_CATEGORICAS,
        "umbral": UMBRAL,
        "referencia_razones": referencia,
        "sha256_onnx": hashlib.sha256((SALIDA / "modelo.onnx").read_bytes()).hexdigest(),   # RF7
        "conjuntos": {"entrenamiento": [len(entrenamiento), int(y_tr.sum())],
                      "validacion": [len(validacion), int(y_val.sum())],
                      "prueba_fija": [len(prueba), int(y_te.sum())]},
        "pr_auc_validacion": pr_auc_validacion,
        "metricas_prueba": metricas,
        "linea_base_logreg": linea_base,
        "costos_usd": {"fraude": COSTO_FRAUDE_USD, "falso_positivo": COSTO_FALSO_POSITIVO_USD},
        "compuerta": control,
        "advertencia": "muestra etiquetada: ~11 % de fraude contra 0,012 % en produccion (R3); "
                       "las probabilidades no son las de produccion",
    }
    if REPORTE_LIMPIEZA.exists():
        metadata["limpieza"] = json.loads(REPORTE_LIMPIEZA.read_text(encoding="utf-8"))["reglas"]
    (SALIDA / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    tam = sum(f.stat().st_size for f in SALIDA.glob("*.*")) / 1024
    print(f"OK: exportado {version} -> {SALIDA} ({tam:,.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
