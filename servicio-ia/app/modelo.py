"""Carga del modelo, inferencia y razones (componente "modelo" de la figura 5, C4 nivel 3).

El modelo llega como tres archivos que deja servicio-ia/entrenar.py en model/:
    preprocesador.joblib   escalado y one-hot (scikit-learn, misma version que en el entrenamiento)
    modelo.onnx            la red entrenada con TensorFlow, exportada a ONNX
    metadata.json          version, umbral, metricas, valores tipicos para las razones, hash del ONNX
Se sirve con onnxruntime: la imagen NO lleva TensorFlow.
"""
from __future__ import annotations

import json
import logging
import os
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

log = logging.getLogger("servicio-ia")

CARPETA_MODELO = Path(os.getenv("MODEL_DIR", Path(__file__).resolve().parent.parent / "model"))

# Nombres legibles para las razones: las lee un analista o un auditor no tecnico (RT8).
NOMBRES = {
    "monto_usd": "monto (USD)",
    "hora_local": "hora local",
    "intentos_previos_1h": "intentos del usuario en la ultima hora",
    "dispositivo_usuarios_distintos": "usuarios distintos en el dispositivo",
    "sin_dispositivo": "sin dispositivo identificado",
    "pais_extranjero": "pais distinto de Chile",
    "canal": "canal",
}


def _preprocesador_numpy(prep):
    """Traduce el ColumnTransformer ajustado (imputar mediana, log1p, estandarizar, one-hot) a funciones
    numpy con sus mismos parametros. Si encuentra un paso que no conoce, devuelve None (se usa scikit-learn)."""
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

    pasos = []
    for nombre, trans, cols in prep.transformers_:
        if nombre == "remainder" or trans == "drop":
            continue
        if isinstance(trans, OneHotEncoder):
            categorias = [list(c) for c in trans.categories_]
            def one_hot(filas, cols=cols, categorias=categorias):
                return np.hstack([np.array([[1.0 if f[c] == k else 0.0 for k in cats] for f in filas])
                                  for c, cats in zip(cols, categorias)])
            pasos.append(one_hot)
            continue
        etapas = trans.steps if isinstance(trans, Pipeline) else [(None, trans)]
        funciones = []
        for _, e in etapas:
            if isinstance(e, SimpleImputer) and e.strategy in ("median", "mean"):
                funciones.append(lambda x, m=e.statistics_: np.where(np.isnan(x), m, x))
            elif isinstance(e, FunctionTransformer) and e.inverse_func is None and e.kw_args is None:
                funciones.append(e.func)
            elif isinstance(e, StandardScaler):
                media = e.mean_ if e.with_mean else 0.0
                escala = e.scale_ if e.with_std else 1.0
                funciones.append(lambda x, a=media, b=escala: (x - a) / b)
            else:
                return None
        def numerico(filas, cols=cols, funciones=funciones):
            x = np.array([[np.nan if f[c] is None else float(f[c]) for c in cols] for f in filas])
            for fn in funciones:
                x = fn(x)
            return x
        pasos.append(numerico)
    return pasos


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


class Modelo:
    def __init__(self) -> None:
        self.metadata: dict = {}
        self._prep = None
        self._rapido = None          # los mismos parametros del preprocesador, aplicados con numpy
        self._sesion = None
        self._entrada = None

    @property
    def cargado(self) -> bool:
        return self._sesion is not None

    @property
    def version(self) -> str | None:
        return self.metadata.get("version")

    @property
    def hash(self) -> str:
        return self.metadata.get("sha256_onnx", "")

    @property
    def umbral(self) -> float:
        return float(self.metadata.get("umbral", 0.5))

    @property
    def columnas(self) -> list[str]:
        return self.metadata["variables_numericas"] + self.metadata["variables_categoricas"]

    def cargar(self, carpeta: Path = CARPETA_MODELO) -> None:
        import onnxruntime as ort

        self.metadata = json.loads((carpeta / "metadata.json").read_text(encoding="utf-8"))
        entrenado_con = self.metadata.get("sklearn_version")
        if entrenado_con and entrenado_con != sklearn.__version__:
            log.warning("Preprocesador creado con scikit-learn %s y servido con %s: "
                        "las predicciones pueden diferir", entrenado_con, sklearn.__version__)
        self._prep = joblib.load(carpeta / "preprocesador.joblib")
        opciones = ort.SessionOptions()
        opciones.intra_op_num_threads = int(os.getenv("ORT_THREADS", "1"))   # 1 hilo: cpus 0.5 en el compose
        opciones.inter_op_num_threads = 1
        self._sesion = ort.InferenceSession(str(carpeta / "modelo.onnx"), opciones, providers=["CPUExecutionProvider"])
        self._entrada = self._sesion.get_inputs()[0].name
        self._rapido = _preprocesador_numpy(self._prep)
        if self._rapido is not None:
            prueba = [{**self.metadata["referencia_razones"], "monto_usd": m, "canal": c}
                      for m, c in [(1.0, "app"), (52.0, "web"), (900.0, "pos"), (12.5, "api"), (3.0, "otro")]]
            with warnings.catch_warnings():          # "otro" es un canal desconocido a proposito
                warnings.simplefilter("ignore")
                esperado = self._prep.transform(pd.DataFrame(prueba, columns=self.columnas))
            if not np.allclose(self._transformar(prueba), esperado, atol=1e-5):
                log.warning("El preprocesador con numpy no coincide con scikit-learn: se usa scikit-learn")
                self._rapido = None
        log.info("Modelo %s (%s) cargado desde %s, umbral %.2f, preprocesador %s",
                 self.version, self.metadata.get("framework"), carpeta, self.umbral,
                 "numpy (verificado contra scikit-learn)" if self._rapido else "scikit-learn")

    def _transformar(self, filas: list[dict]) -> np.ndarray:
        """Preprocesamiento. scikit-learn tarda ~4 ms por llamada en validar la entrada (medido); con los
        MISMOS parametros aplicados con numpy tarda ~0,05 ms. Al cargar se comprueba que den lo mismo."""
        if self._rapido is None:
            return np.asarray(self._prep.transform(pd.DataFrame(filas, columns=self.columnas)), dtype=np.float32)
        return np.hstack([paso(filas) for paso in self._rapido]).astype(np.float32)

    def _probabilidades(self, filas: list[dict]) -> np.ndarray:
        x = self._transformar(filas)
        return np.asarray(self._sesion.run(None, {self._entrada: x})[0], dtype=float).reshape(-1)

    def predecir(self, filas: list[dict]) -> list[float]:
        return self._probabilidades(filas).tolist()

    def predecir_con_razones(self, fila: dict) -> tuple[float, list[dict]]:
        """Probabilidad y aporte de cada variable a ESTA prediccion (RF2).

        La red no reparte el puntaje por variable como los arboles, asi que se mide por oclusion: se cambia
        una variable a su valor tipico (mediana del entrenamiento, en metadata.json) y se ve cuanto se mueve
        el riesgo. Las 8 filas (original + 7 cambiadas) van en UNA sola llamada a onnxruntime."""
        referencia = self.metadata["referencia_razones"]
        variantes = [fila] + [{**fila, v: referencia[v]} for v in self.columnas]
        probs = self._probabilidades(variantes)
        logits = _logit(probs)
        razones = []
        for i, v in enumerate(self.columnas, start=1):
            aporte = float(logits[0] - logits[i])
            if fila[v] == referencia[v] or abs(aporte) < 0.01:
                continue
            efecto = "sube" if aporte > 0 else "baja"
            razones.append({"variable": v, "valor": fila[v], "efecto": efecto, "aporte": round(aporte, 3),
                            "condicion": f"{NOMBRES[v]} = {self._texto(v, fila[v])} ({efecto} el riesgo)"})
        razones.sort(key=lambda r: abs(r["aporte"]), reverse=True)
        return float(probs[0]), razones

    @staticmethod
    def _texto(variable: str, valor) -> str:
        if variable in ("sin_dispositivo", "pais_extranjero"):
            return "si" if valor else "no"
        if variable == "dispositivo_usuarios_distintos" and valor == -1:
            return "desconocido"
        return valor if isinstance(valor, str) else f"{valor:g}"

    def nivel(self, p: float) -> str:
        if p < self.umbral:
            return "bajo"
        return "alto" if p >= max(0.8, self.umbral) else "medio"


modelo = Modelo()
