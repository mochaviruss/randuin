"""Tests del Motor de Inferencia. El pipeline los corre DESPUES de entrenar y ANTES del docker build.

    cd servicio-ia && pytest -q          (o desde la raiz: python -m pytest -q servicio-ia/tests)
"""
import sys

from fastapi.testclient import TestClient

from app.esquemas import EJEMPLO
from app.main import app

# Compra normal: primer intento, dispositivo propio, monto bajo, de dia, en la app.
NORMAL = {"monto_usd": 9.5, "hora_local": 14, "intentos_previos_1h": 0, "dispositivo_usuarios_distintos": 1,
          "sin_dispositivo": False, "pais_extranjero": False, "canal": "app"}


def cliente():
    return TestClient(app)   # como context manager, para que corra el lifespan (carga del modelo)


def test_health_con_modelo_cargado():
    with cliente() as c:
        r = c.get("/health")
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["modelo_cargado"] is True and cuerpo["modelo_version"]
        assert cuerpo["framework"].startswith("tensorflow")


def test_predict_sospechosa_es_fraude_y_trae_razones():
    # Patron de fraude del caso: muchos intentos en la ultima hora, dispositivo compartido, de madrugada, web.
    with cliente() as c:
        r = c.post("/predict", json=EJEMPLO)
        assert r.status_code == 200
        cuerpo = r.json()
        assert 0.0 <= cuerpo["score"] <= 1.0
        assert cuerpo["prediccion"] == "fraude" and cuerpo["nivel_riesgo"] == "alto"
        assert cuerpo["transaccion_id"] == "TX-EJEMPLO"
        assert cuerpo["modelo_version"] and len(cuerpo["modelo_hash"]) == 64
        assert r.headers["X-Version-Modelo"] == cuerpo["modelo_version"]
        # RF2: las 3 razones de mayor peso; la primera empuja el riesgo hacia arriba
        assert 1 <= len(cuerpo["razones"]) <= 3 and len(cuerpo["aportes"]) >= len(cuerpo["razones"])
        assert cuerpo["razones"][0]["efecto"] == "sube"
        assert "intentos_previos_1h" in [x["variable"] for x in cuerpo["razones"]]
        pesos = [abs(x["aporte"]) for x in cuerpo["aportes"]]
        assert pesos == sorted(pesos, reverse=True)


def test_predict_normal_es_legitima():
    with cliente() as c:
        cuerpo = c.post("/predict", json=NORMAL).json()
        assert cuerpo["prediccion"] == "legitima" and cuerpo["score"] < 0.3


def test_contrato_invalido_da_422():
    malas = [{**NORMAL, "monto_usd": -5}, {**NORMAL, "hora_local": 25}, {**NORMAL, "canal": "fax"},
             {**NORMAL, "campo_extra": 1}, {"hora_local": 10}]
    with cliente() as c:
        for m in malas:
            assert c.post("/predict", json=m).status_code == 422, m


def test_predict_lote():
    with cliente() as c:
        r = c.post("/predict/lote", json={"transacciones": [EJEMPLO, NORMAL]})
        assert r.status_code == 200
        assert r.json()["total"] == 2 and r.json()["alertas"] == 1


def test_metricas_prometheus():
    with cliente() as c:
        c.post("/predict", json=NORMAL)
        texto = c.get("/metrics/").text
        assert "randuin_ia_predict_segundos" in texto and "randuin_ia_predicciones_total" in texto


def test_la_imagen_no_necesita_tensorflow():
    """Servir el modelo no importa TensorFlow ni PyTorch (por eso la imagen pesa poco)."""
    with cliente() as c:
        c.post("/predict", json=EJEMPLO)
    assert "tensorflow" not in sys.modules and "torch" not in sys.modules and "keras" not in sys.modules


def test_sin_modelo_health_da_503(tmp_path, monkeypatch):
    from app import modelo as m
    monkeypatch.setattr(m, "CARPETA_MODELO", tmp_path)          # carpeta vacia: no hay modelo
    vacio = m.Modelo()
    monkeypatch.setattr("app.main.modelo", vacio)
    monkeypatch.setattr(vacio, "cargar", lambda: m.Modelo.cargar(vacio, tmp_path))
    with cliente() as c:
        assert c.get("/health").status_code == 503
        assert c.post("/predict", json=NORMAL).status_code == 503
