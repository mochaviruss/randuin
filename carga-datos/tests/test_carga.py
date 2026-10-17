"""Tests del servicio de carga.
- Siempre: el esquema crea todas las tablas y cada INSERT lleva ON CONFLICT DO NOTHING.
- Con una base real (TEST_DB_URL, la pone el pipeline): cargar dos veces no duplica filas."""
import os

import pytest

import cargar


def test_el_esquema_crea_todas_las_tablas():
    esquema = (cargar.AQUI / "schema.sql").read_text(encoding="utf-8")
    for tabla in cargar.TABLAS + ["umbrales", "predicciones"]:
        assert f"CREATE TABLE IF NOT EXISTS {tabla} " in esquema


def test_el_insert_no_duplica():
    assert cargar.sql_insercion("usuarios", ["usuario_id", "pais"]).endswith("ON CONFLICT DO NOTHING")


@pytest.mark.skipif(not os.getenv("TEST_DB_URL"), reason="sin base de prueba (TEST_DB_URL)")
def test_cargar_dos_veces_no_duplica(monkeypatch):
    monkeypatch.setattr(cargar, "DB_URL", os.environ["TEST_DB_URL"])
    primera = cargar.main()
    segunda = cargar.main()
    for tabla in cargar.TABLAS:
        assert segunda[tabla]["nuevas"] == 0, f"{tabla}: la segunda carga inserto filas"
        assert segunda[tabla]["total"] == primera[tabla]["total"]
