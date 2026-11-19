"""Script que corre DENTRO del Training Job (contenedor de TensorFlow de AWS). No se ejecuta a mano: lo lanza 2_entrenar.py.

Entrena la MISMA red de EP2 (docs/ELECCION-MODELO.md): MLP 64-32, dropout 0,2, Adam 1e-3, 60 epocas, lotes de 64,
sin peso de clase, semilla 42. Lo unico que cambia es DONDE vive el escalado:

    EP2   preprocesador.joblib (scikit-learn) en el servicio de IA  +  modelo.onnx
    EP3   el log del monto y la estandarizacion son CAPAS DE LA RED y viajan dentro del modelo guardado.
          El endpoint recibe las 10 columnas tal como salen de vectorizar() y el servicio de IA no necesita
          scikit-learn, pandas ni onnxruntime.

Entradas (las deja SageMaker): SM_CHANNEL_TRAIN/train.csv y SM_CHANNEL_VALIDATION/validation.csv, sin encabezado,
10 columnas de entrada y es_fraude al final. Salida: SM_MODEL_DIR/export/Servo/1 (un SavedModel para TensorFlow
Serving) y SM_OUTPUT_DATA_DIR/metricas.json.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np

# Las columnas de entrada, en orden (igual que sagemaker/comun_sm.py:COLUMNAS):
#   0 monto_usd | 1..5 hora_local, intentos_previos_1h, dispositivo_usuarios_distintos, sin_dispositivo,
#   pais_extranjero | 6..9 canal_app, canal_web, canal_pos, canal_api
N_ENTRADAS = 10


def cargar(ruta: Path):
    datos = np.loadtxt(ruta, delimiter=",", dtype=np.float32, ndmin=2)
    return datos[:, :N_ENTRADAS], datos[:, N_ENTRADAS]


def pr_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Area bajo la curva precision-recall (average precision), como sklearn.average_precision_score."""
    orden = np.argsort(-p, kind="stable")
    y = y[orden]
    tp = np.cumsum(y)
    precision = tp / np.arange(1, len(y) + 1)
    return float((precision * y).sum() / max(y.sum(), 1))


def media_y_varianza(x: np.ndarray):
    """Igual que StandardScaler de EP2: media y varianza de la poblacion (ddof 0); varianza 0 -> 1."""
    media, var = x.mean(axis=0), x.var(axis=0)
    return media.tolist(), np.where(var == 0, 1.0, var).tolist()


def construir(X_tr: np.ndarray, capas: list, dropout: float, lr: float):
    import tensorflow as tf
    keras = tf.keras

    m_monto, v_monto = media_y_varianza(np.log1p(X_tr[:, 0:1]))     # el monto va en escala logaritmica (de USD 1 a miles)
    m_num, v_num = media_y_varianza(X_tr[:, 1:6])

    x = keras.Input(shape=(N_ENTRADAS,), name="x")
    monto = keras.layers.Lambda(lambda t: tf.math.log1p(t[:, 0:1]), name="log_monto")(x)
    monto = keras.layers.Normalization(mean=m_monto, variance=v_monto, name="escala_monto")(monto)
    num = keras.layers.Lambda(lambda t: t[:, 1:6], name="numericas")(x)
    num = keras.layers.Normalization(mean=m_num, variance=v_num, name="escala_numericas")(num)
    canal = keras.layers.Lambda(lambda t: t[:, 6:10], name="canal")(x)           # ya viene en 0/1
    h = keras.layers.Concatenate(name="vector")([monto, num, canal])
    for n in capas:
        h = keras.layers.Dense(n, activation="relu")(h)
        h = keras.layers.Dropout(dropout)(h)
    salida = keras.layers.Dense(1, activation="sigmoid", name="probabilidad_fraude")(h)
    red = keras.Model(x, salida)
    red.compile(optimizer=keras.optimizers.Adam(lr), loss="binary_crossentropy")
    return red


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capas", default="64,32")
    ap.add_argument("--dropout", type=float, default=0.2)
    ap.add_argument("--epocas", type=int, default=60)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--lote", type=int, default=64)
    ap.add_argument("--semilla", type=int, default=42)
    args, _ = ap.parse_known_args()          # SageMaker agrega argumentos propios: se ignoran

    entrada = Path(os.environ.get("SM_CHANNEL_TRAIN", "/opt/ml/input/data/train"))
    validacion = Path(os.environ.get("SM_CHANNEL_VALIDATION", "/opt/ml/input/data/validation"))
    modelo_dir = Path(os.environ.get("SM_MODEL_DIR", "/opt/ml/model"))
    salida_dir = Path(os.environ.get("SM_OUTPUT_DATA_DIR", "/opt/ml/output/data"))

    import tensorflow as tf
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    tf.keras.utils.set_random_seed(args.semilla)
    tf.config.experimental.enable_op_determinism()          # la misma red en cada ejecucion

    X_tr, y_tr = cargar(entrada / "train.csv")
    X_val, y_val = cargar(validacion / "validation.csv")
    capas = [int(n) for n in args.capas.split(",") if n.strip()]
    print(f"train {len(X_tr)} filas ({int(y_tr.sum())} fraudes) | validation {len(X_val)} ({int(y_val.sum())})")

    red = construir(X_tr, capas, args.dropout, args.lr)
    red.fit(X_tr, y_tr, epochs=args.epocas, batch_size=args.lote, verbose=0)      # sin peso de clase (Riesgo pide menos alertas)

    p_val = red.predict(X_val, verbose=0).ravel()
    p_tr = red.predict(X_tr, verbose=0).ravel()
    aucpr_val, aucpr_tr = pr_auc(y_val, p_val), pr_auc(y_tr, p_tr)
    # Esta linea la lee SageMaker con la expresion regular de 2_entrenar.py (MetricDefinitions).
    print(f"validation_aucpr={aucpr_val:.4f}")
    print(f"train_aucpr={aucpr_tr:.4f}")

    destino = modelo_dir / "export" / "Servo" / "1"          # el formato que espera TensorFlow Serving
    destino.parent.mkdir(parents=True, exist_ok=True)
    red.export(str(destino))
    salida_dir.mkdir(parents=True, exist_ok=True)
    (salida_dir / "metricas.json").write_text(json.dumps({
        "validation_aucpr": round(aucpr_val, 4), "train_aucpr": round(aucpr_tr, 4),
        "hiperparametros": vars(args), "tensorflow": tf.__version__,
        "filas": {"train": len(X_tr), "validation": len(X_val)}}, indent=2))
    print(f"OK: modelo exportado en {destino}")


if __name__ == "__main__":
    main()
