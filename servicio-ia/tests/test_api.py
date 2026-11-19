"""Tests del microservicio de IA (EP3). El endpoint de SageMaker se simula con un doble: no hacen falta credenciales
de AWS ni red, asi que corren en el pipeline antes de construir la imagen.

    cd servicio-ia && pytest -q          (o desde la raiz: python -m pytest -q servicio-ia/tests)

La calidad del modelo se mide aparte, contra el endpoint real: sagemaker/4_probar.py.
"""
import io
import json
import math
import random
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import modelo as m
from app.esquemas import EJEMPLO
from app.main import app

# Compra normal: primer intento, dispositivo propio, monto bajo, de dia, en la app.
NORMAL = {"monto_usd": 9.5, "hora_local": 14, "intentos_previos_1h": 0, "dispositivo_usuarios_distintos": 1,
          "sin_dispositivo": False, "pais_extranjero": False, "canal": "app"}


class EndpointFalso:
    """Hace de sagemaker-runtime: recibe {"instances": [[10 numeros], ...]} y responde como TensorFlow Serving.
    La 'red' es una logistica simple: mas intentos, mas usuarios en el dispositivo y pais extranjero suben el riesgo."""

    def __init__(self):
        self.llamadas = []          # filas por invocacion
        self.caido = False

    @staticmethod
    def puntaje(x):
        z = -4 + 0.7 * x[2] + 0.5 * max(x[3], 0) + 1.5 * x[5] + 0.01 * x[0]
        return 1 / (1 + math.exp(-z))

    def invoke_endpoint(self, EndpointName, ContentType, Accept, Body):
        if self.caido:
            raise RuntimeError("ThrottlingException simulado")
        filas = json.loads(Body)["instances"]
        assert all(len(f) == 10 for f in filas), "el endpoint espera 10 numeros por transaccion"
        self.llamadas.append(len(filas))
        cuerpo = {"predictions": [[self.puntaje(f)] for f in filas]}
        return {"Body": io.BytesIO(json.dumps(cuerpo).encode())}


class SageMakerFalso:
    def __init__(self):
        self.caido = False

    def describe_endpoint(self, EndpointName):
        if self.caido:
            raise RuntimeError("endpoint borrado")
        return {"EndpointConfigName": "randuin-fraude-PRUEBA", "EndpointStatus": "InService"}


@pytest.fixture
def endpoint():
    return EndpointFalso()


@pytest.fixture
def cliente(endpoint, monkeypatch):
    modelo = m.Modelo(runtime=endpoint, sagemaker=SageMakerFalso())
    modelo.cargar = lambda: m.Modelo.cargar(modelo, refrescar_en_segundo_plano=False)     # sin hilo de fondo
    monkeypatch.setattr("app.main.modelo", modelo)
    with TestClient(app) as c:        # como context manager, para que corra el lifespan (lee la referencia)
        c.modelo = modelo
        yield c


def test_health_informa_modelo_y_estado_del_endpoint(cliente):
    r = cliente.get("/health")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["modelo_cargado"] is True and cuerpo["modelo_version"] == "randuin-fraude-PRUEBA"
    assert cuerpo["motor"] == "sagemaker" and cuerpo["sagemaker"] == "InService"
    assert cuerpo["framework"].startswith("tensorflow")


def test_predict_sospechosa_es_fraude_y_trae_razones(cliente):
    # Patron de fraude del caso: muchos intentos en la ultima hora, dispositivo compartido, de madrugada, web.
    r = cliente.post("/predict", json=EJEMPLO)
    assert r.status_code == 200
    cuerpo = r.json()
    assert 0.0 <= cuerpo["score"] <= 1.0
    assert cuerpo["prediccion"] == "fraude" and cuerpo["nivel_riesgo"] == "alto"
    assert cuerpo["transaccion_id"] == "TX-EJEMPLO"
    assert cuerpo["modelo_version"] == "randuin-fraude-PRUEBA" and cuerpo["replica"]
    assert r.headers["X-Version-Modelo"] == cuerpo["modelo_version"]
    # RF2: las 3 razones de mayor peso; la primera empuja el riesgo hacia arriba
    assert 1 <= len(cuerpo["razones"]) <= 3
    assert cuerpo["razones"][0]["efecto"] == "sube"
    assert "intentos_previos_1h" in [x["variable"] for x in cuerpo["razones"]]
    pesos = [abs(x["aporte"]) for x in cuerpo["razones"]]
    assert pesos == sorted(pesos, reverse=True)


def test_predict_normal_es_legitima(cliente):
    cuerpo = cliente.post("/predict", json=NORMAL).json()
    assert cuerpo["prediccion"] == "legitima" and cuerpo["score"] < 0.3


def test_las_razones_salen_de_una_sola_invocacion_de_8_filas(cliente, endpoint):
    """La original y las 7 variantes por oclusion van juntas: una ida a SageMaker, no ocho (RNF1)."""
    cliente.post("/predict", json=EJEMPLO)
    assert endpoint.llamadas == [8]


def test_contrato_invalido_da_422_y_no_llega_a_sagemaker(cliente, endpoint):
    malas = [{**NORMAL, "monto_usd": -5}, {**NORMAL, "hora_local": 25}, {**NORMAL, "canal": "fax"},
             {**NORMAL, "campo_extra": 1}, {"hora_local": 10}]
    for mala in malas:
        assert cliente.post("/predict", json=mala).status_code == 422, mala
    assert endpoint.llamadas == []


def test_predict_lote(cliente):
    r = cliente.post("/predict/lote", json={"transacciones": [EJEMPLO, NORMAL]})
    assert r.status_code == 200
    assert r.json()["total"] == 2 and r.json()["alertas"] == 1


def test_si_sagemaker_no_responde_predict_da_503_y_el_servicio_sigue_sano(cliente, endpoint):
    """El dominio traduce este 503 en su politica de reserva (RNF3). Y /health sigue en 200: que SageMaker se caiga
    no es falla de este contenedor, asi que Docker no lo reinicia en bucle."""
    endpoint.caido = True
    r = cliente.post("/predict", json=NORMAL)
    assert r.status_code == 503 and "SageMaker" in r.json()["detail"]
    assert cliente.get("/health").status_code == 200


def test_health_muestra_endpoint_caido_sin_tumbar_el_servicio(cliente):
    cliente.modelo._sagemaker_cliente.caido = True
    cliente.modelo.refrescar()
    cuerpo = cliente.get("/health").json()
    assert cuerpo["estado"] == "ok" and cuerpo["sagemaker"].startswith("error")


def test_el_vector_que_se_envia_es_el_del_entrenamiento():
    """sagemaker/comun_sm.py arma la entrada del entrenamiento; app/modelo.py arma la del servicio. Si difieren,
    la red recibe columnas cambiadas y predice mal sin dar ningun error."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sagemaker"))
    import comun_sm
    assert m.VARIABLES == comun_sm.NUMERICAS + ["canal"]
    azar = random.Random(7)
    for _ in range(50):
        fila = {"monto_usd": round(azar.uniform(1, 5000), 2), "hora_local": azar.randint(0, 23),
                "intentos_previos_1h": azar.randint(0, 9), "dispositivo_usuarios_distintos": azar.randint(-1, 8),
                "sin_dispositivo": azar.randint(0, 1), "pais_extranjero": azar.randint(0, 1),
                "canal": azar.choice(comun_sm.CANALES)}
        assert m.vectorizar(fila) == comun_sm.vectorizar(fila)


def test_la_imagen_no_necesita_el_modelo_ni_librerias_de_ia(cliente):
    """Servir no importa TensorFlow, onnxruntime, scikit-learn, pandas ni numpy (por eso la imagen pesa poco)."""
    cliente.post("/predict", json=EJEMPLO)
    pesadas = {"tensorflow", "keras", "torch", "onnxruntime", "sklearn", "pandas", "numpy"}
    assert not pesadas & set(sys.modules)


def test_sin_referencia_health_da_503(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "REFERENCIA", tmp_path / "no-existe.json")
    vacio = m.Modelo(runtime=EndpointFalso(), sagemaker=SageMakerFalso())
    monkeypatch.setattr("app.main.modelo", vacio)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 503
        assert c.post("/predict", json=NORMAL).status_code == 503
