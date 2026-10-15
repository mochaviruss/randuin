"""Carga del modelo, inferencia y razones (componente "modelo" de la figura 5, C4 nivel 3).

El modelo llega como tres archivos que deja servicio-ia/entrenar.py en model/ (igual que en la guia del curso):
    preprocesador.joblib   escalado y one-hot (scikit-learn, misma version que en el entrenamiento)
    modelo.onnx            la red entrenada con TensorFlow, exportada a ONNX
    metadata.json          version, umbral, metricas y valores tipicos para las razones
Se sirve con onnxruntime: la imagen NO lleva TensorFlow.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

log = logging.getLogger("servicio-ia")

CARPETA_MODELO = Path(os.getenv("MODEL_DIR", Path(__file__).resolve().parent.parent / "model"))

# Nombres legibles para las razones: las lee un analista o un auditor (RF2).
NOMBRES = {
    "monto_usd": "monto (USD)",
    "hora_local": "hora local",
    "intentos_previos_1h": "intentos del usuario en la ultima hora",
    "dispositivo_usuarios_distintos": "usuarios distintos en el dispositivo",
    "sin_dispositivo": "sin dispositivo identificado",
    "pais_extranjero": "pais distinto de Chile",
    "canal": "canal",
}


class Modelo:
    def __init__(self) -> None:
        self.metadata: dict = {}
        self._prep = None
        self._sesion = None

    @property
    def cargado(self) -> bool:
        return self._sesion is not None

    @property
    def version(self) -> str | None:
        return self.metadata.get("version")

    @property
    def umbral(self) -> float:
        return float(self.metadata.get("umbral", 0.5))

    @property
    def columnas(self) -> list[str]:
        return self.metadata["variables_numericas"] + self.metadata["variables_categoricas"]

    def cargar(self, carpeta: Path = CARPETA_MODELO) -> None:
        import onnxruntime as ort

        self.metadata = json.loads((carpeta / "metadata.json").read_text(encoding="utf-8"))
        if self.metadata.get("sklearn_version") != sklearn.__version__:
            log.warning("Preprocesador creado con scikit-learn %s y servido con %s",
                        self.metadata.get("sklearn_version"), sklearn.__version__)
        self._prep = joblib.load(carpeta / "preprocesador.joblib")
        opciones = ort.SessionOptions()
        opciones.intra_op_num_threads = 1          # 1 hilo por prediccion (lo mas simple; ver 7.2)
        self._sesion = ort.InferenceSession(str(carpeta / "modelo.onnx"), opciones, providers=["CPUExecutionProvider"])
        log.info("Modelo %s (%s) cargado desde %s", self.version, self.metadata.get("framework"), carpeta)

    def probabilidades(self, filas: list[dict]) -> np.ndarray:
        x = np.asarray(self._prep.transform(pd.DataFrame(filas, columns=self.columnas)), dtype=np.float32)
        entrada = self._sesion.get_inputs()[0].name
        return np.asarray(self._sesion.run(None, {entrada: x})[0], dtype=float).reshape(-1)

    def predecir_con_razones(self, fila: dict) -> tuple[float, list[dict]]:
        """Probabilidad y razones de ESTA prediccion (RF2).

        Se cambia una variable a la vez por su valor tipico del entrenamiento (guardado en metadata.json) y se
        mira cuanto cambia la probabilidad. Lo que mas la mueve es la razon de mas peso. Las 8 filas (la original
        y las 7 cambiadas) se calculan en una sola llamada al modelo."""
        tipico = self.metadata["valores_tipicos"]
        variantes = [fila] + [{**fila, v: tipico[v]} for v in self.columnas]
        probs = self.probabilidades(variantes)
        razones = []
        for i, v in enumerate(self.columnas, start=1):
            aporte = float(probs[0] - probs[i])          # cuanto sube (o baja) el riesgo por el valor real
            if fila[v] == tipico[v] or abs(aporte) < 0.005:
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
        return "alto" if p >= 0.7 else "medio"


modelo = Modelo()
