"""Cliente del modelo (EP3): la red vive en un endpoint de Amazon SageMaker y este servicio la INVOCA.

EP2   la red (TensorFlow -> ONNX) iba dentro de la imagen y se cargaba con onnxruntime
EP3   la misma red 64-32 corre en un endpoint serverless (sagemaker/); aqui solo se arma la entrada, se invoca con
      boto3 (sagemaker-runtime, invoke_endpoint) y se calculan las razones. Sin TensorFlow, onnxruntime, scikit-learn
      ni pandas: la imagen queda liviana.

Credenciales: el rol de la EC2 (LabInstanceProfile). boto3 las obtiene solo; no hay access keys en el codigo, la imagen
ni el .env (EP3, 2.2 regla 4).

El escalado (log del monto, estandarizacion) va DENTRO de la red: el endpoint recibe 10 numeros por transaccion, los
mismos que produce sagemaker/comun_sm.py:vectorizar. Un test comprueba que los dos vectorizar dan lo mismo.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("servicio-ia")

REFERENCIA = Path(os.getenv("REFERENCIA", Path(__file__).resolve().parent / "referencia.json"))
ENDPOINT = os.getenv("ENDPOINT", "randuin-fraude")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
# Cuanto se espera a SageMaker. 10 s cubren un cold start (~6 s medido en el Learner Lab). El dominio no espera tanto
# (IA_TIMEOUT_MS): aplica su politica de reserva y esta llamada igual termina, dejando el contenedor caliente.
TIMEOUT_S = float(os.getenv("SAGEMAKER_TIMEOUT", "10"))
UMBRAL = float(os.getenv("UMBRAL_MODELO", "0.30"))
REFRESCO_S = 60          # cada cuanto se pregunta a SageMaker por la version y el estado (en segundo plano)

# Las 7 variables de POST /predict, en el orden en que entran a la red (igual que sagemaker/comun_sm.py).
NUMERICAS = ["monto_usd", "hora_local", "intentos_previos_1h", "dispositivo_usuarios_distintos",
             "sin_dispositivo", "pais_extranjero"]
CANALES = ["app", "web", "pos", "api"]
VARIABLES = NUMERICAS + ["canal"]

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


def vectorizar(fila: dict) -> list[float]:
    """Una transaccion (7 variables) -> los 10 numeros que recibe el endpoint (canal en 4 columnas 0/1)."""
    canal = str(fila["canal"])
    return [float(fila[v]) for v in NUMERICAS] + [1.0 if canal == c else 0.0 for c in CANALES]


class ErrorModelo(Exception):
    """SageMaker no respondio (caido, lento, sin permiso o con una respuesta que no se entiende)."""


class Modelo:
    def __init__(self, runtime=None, sagemaker=None) -> None:
        self.metadata: dict = {}
        self._runtime_cliente = runtime            # se inyectan en los tests; en produccion se crean con boto3
        self._sagemaker_cliente = sagemaker
        self._version: str | None = None
        self._estado: str = "sin consultar"

    # --- estado ---------------------------------------------------------------------------------------------------
    @property
    def cargado(self) -> bool:
        """True si ya se leyo la referencia. NO depende de SageMaker: si el endpoint se cae, el servicio sigue sano
        (responde 503 en /predict) y Docker no lo reinicia en bucle."""
        return bool(self.metadata)

    @property
    def version(self) -> str | None:
        return self._version

    @property
    def estado_endpoint(self) -> str:
        return self._estado

    @property
    def umbral(self) -> float:
        return UMBRAL

    @property
    def columnas(self) -> list[str]:
        return VARIABLES

    def cargar(self, refrescar_en_segundo_plano: bool = True) -> None:
        """Lee la referencia (valores tipicos para las razones, medias del entrenamiento) y consulta el endpoint una vez."""
        referencia = json.loads(REFERENCIA.read_text(encoding="utf-8"))
        self.metadata = {
            "nombre": "randuin-fraude", "formato": "SavedModel (TensorFlow Serving)", "endpoint": ENDPOINT,
            "framework": "tensorflow (red 64-32 en un endpoint de SageMaker)", "umbral": UMBRAL, **referencia}
        self.refrescar()
        if refrescar_en_segundo_plano:
            threading.Thread(target=self._bucle_refresco, daemon=True, name="refresco-endpoint").start()
        log.info("Referencia cargada; endpoint %s: %s (version %s)", ENDPOINT, self._estado, self._version)

    def refrescar(self) -> None:
        """Pregunta a SageMaker la version (nombre de la configuracion del endpoint) y su estado. Nunca lanza."""
        try:
            d = self._sagemaker().describe_endpoint(EndpointName=ENDPOINT)
            self._version, self._estado = d["EndpointConfigName"], d["EndpointStatus"]
        except Exception as e:               # sin credenciales, sin red o endpoint borrado
            self._estado = f"error: {type(e).__name__}"

    def _bucle_refresco(self) -> None:
        while True:
            time.sleep(REFRESCO_S)
            self.refrescar()

    # --- clientes boto3 (se importan aqui: los tests y el arranque sin AWS no los necesitan) -------------------------
    def _runtime(self):
        if self._runtime_cliente is None:
            import boto3
            from botocore.config import Config
            # Sin reintentos de boto3 y con tiempo maximo: si SageMaker no responde se sabe rapido y el dominio
            # aplica su politica de reserva (RNF3).
            self._runtime_cliente = boto3.client(
                "sagemaker-runtime", region_name=REGION,
                config=Config(connect_timeout=2, read_timeout=TIMEOUT_S, retries={"total_max_attempts": 1}))
        return self._runtime_cliente

    def _sagemaker(self):
        if self._sagemaker_cliente is None:
            import boto3
            from botocore.config import Config
            self._sagemaker_cliente = boto3.client(
                "sagemaker", region_name=REGION,
                config=Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1}))
        return self._sagemaker_cliente

    # --- inferencia -------------------------------------------------------------------------------------------------
    def probabilidades(self, filas: list[dict]) -> list[float]:
        """Una invocacion al endpoint con todas las filas. TensorFlow Serving responde {"predictions": [[p], ...]}."""
        cuerpo = json.dumps({"instances": [vectorizar(f) for f in filas]})
        try:
            r = self._runtime().invoke_endpoint(EndpointName=ENDPOINT, ContentType="application/json",
                                                Accept="application/json", Body=cuerpo)
            pred = json.loads(r["Body"].read())["predictions"]
            probs = [float(p[0] if isinstance(p, list) else p) for p in pred]
        except Exception as e:
            raise ErrorModelo(type(e).__name__) from e
        if len(probs) != len(filas):
            raise ErrorModelo(f"SageMaker devolvio {len(probs)} valores para {len(filas)} filas")
        return probs

    def predecir_con_razones(self, fila: dict) -> tuple[float, list[dict]]:
        """Probabilidad y razones de ESTA prediccion (RF2).

        Se cambia una variable a la vez por su valor tipico del entrenamiento (referencia.json) y se mira cuanto
        cambia la probabilidad. Lo que mas la mueve es la razon de mas peso. Las 8 filas (la original y las 7
        cambiadas) van en UNA sola invocacion al endpoint."""
        tipico = self.metadata["valores_tipicos"]
        variantes = [fila] + [{**fila, v: tipico[v]} for v in VARIABLES]
        probs = self.probabilidades(variantes)
        razones = []
        for i, v in enumerate(VARIABLES, start=1):
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
