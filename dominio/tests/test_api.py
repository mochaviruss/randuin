"""Tests de la API contra una base real con los datos cargados (TEST_DB_URL; en el pipeline la carga
la hacen antes los tests de carga-datos). El modelo se reemplaza por una funcion falsa: aqui se prueba el dominio."""
import os

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

DB = os.getenv("TEST_DB_URL")
pytestmark = pytest.mark.skipif(not DB, reason="sin base de prueba (TEST_DB_URL)")


@pytest.fixture()
def cliente():
    from app import main
    with psycopg.connect(DB) as con:
        if con.execute("SELECT count(*) FROM usuarios").fetchone()[0] == 0:
            pytest.skip("la base de prueba no tiene datos: corre antes la carga")
    with TestClient(main.app) as c:        # "with" abre y cierra el pool de conexiones (lifespan)
        yield c, main


def modelo_falso(score):
    return lambda variables, tid: {"score": score, "modelo_version": "test", "replica": "r1",
                                   "razones": [{"variable": "monto_usd", "condicion": "prueba"}]}


def un_usuario():
    with psycopg.connect(DB) as con:
        return con.execute("SELECT usuario_id FROM usuarios ORDER BY usuario_id LIMIT 1").fetchone()[0]


def test_health(cliente):
    c, _ = cliente
    assert c.get("/health").json()["base"] == "ok"


def test_evaluar_con_modelo_decide_por_umbral_y_guarda(cliente, monkeypatch):
    c, main = cliente
    monkeypatch.setattr(main, "pedir_al_modelo", modelo_falso(0.95))
    r = c.post("/evaluar", json={"usuario_id": un_usuario(), "monto": 25000, "canal": "web"})
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["decision"] == "bloquear" and cuerpo["origen"] == "modelo" and cuerpo["modelo_version"] == "test"
    guardada = c.get(f"/predicciones/{cuerpo['transaccion_id']}").json()
    assert guardada["decision"] == "bloquear" and guardada["entrada"]["monto"] == 25000


def test_modelo_caido_aplica_politica_de_reserva(cliente, monkeypatch):
    c, main = cliente

    def caido(variables, tid):
        raise httpx.ConnectTimeout("sin respuesta")
    monkeypatch.setattr(main, "pedir_al_modelo", caido)
    cuerpo = c.post("/evaluar", json={"usuario_id": un_usuario(), "monto": 1000}).json()
    assert cuerpo["origen"] == "reserva" and cuerpo["score"] is None


def test_lista_negra_bloquea_sin_consultar_al_modelo(cliente, monkeypatch):
    c, main = cliente
    monkeypatch.setattr(main, "pedir_al_modelo", lambda *a: pytest.fail("no debia llamar al modelo"))
    ejemplo = c.get("/ejemplos").json()["lista_negra"]
    cuerpo = c.post("/evaluar", json={"usuario_id": un_usuario(), "monto": 1000,
                                      "dispositivo_id": ejemplo["dispositivo_id"]}).json()
    assert cuerpo["decision"] == "bloquear" and cuerpo["origen"] == "lista_negra"


def test_validaciones(cliente):
    c, _ = cliente
    assert c.post("/evaluar", json={"usuario_id": "NO-EXISTE", "monto": 10}).status_code == 404
    assert c.post("/evaluar", json={"usuario_id": "X", "monto": -5}).status_code == 422
    assert c.post("/umbrales", json={"umbral_revisar": 0.8, "umbral_bloquear": 0.5, "autorizado_por": "Ignacia Bravo",
                                     "motivo": "al reves"}).status_code == 422
    assert c.post("/umbrales", json={"umbral_revisar": 0.3, "umbral_bloquear": 0.7,
                                     "motivo": "sin decir quien"}).status_code == 422
