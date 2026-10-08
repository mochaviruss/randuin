"""Test del tratamiento: los CSV que se cargan a la base no traen duplicados ni nulos en columnas clave.
Corre despues de datos/preparar.py (en el pipeline, job datos-y-modelo)."""
from pathlib import Path

import pandas as pd
import pytest

TRATADOS = Path(__file__).resolve().parents[2] / "carga-datos" / "tratados"

# tabla -> (clave primaria, columnas que no pueden venir vacias)
REGLAS = {
    "usuarios": ("usuario_id", ["usuario_id", "pais", "segmento"]),
    "dispositivos": ("dispositivo_id", ["dispositivo_id"]),
    "lista_negra": ("entrada_id", ["entrada_id", "tipo", "valor", "vigente"]),
    "transacciones": ("transaccion_id", ["transaccion_id", "usuario_id", "ts_utc", "monto_usd", "moneda", "canal"]),
}


@pytest.fixture(scope="module", params=list(REGLAS))
def tabla(request):
    return request.param, pd.read_csv(TRATADOS / f"{request.param}.csv", dtype=str, keep_default_na=False)


def test_sin_duplicados(tabla):
    nombre, df = tabla
    clave, _ = REGLAS[nombre]
    assert not df.duplicated().any(), f"{nombre}: filas repetidas"
    assert df[clave].is_unique, f"{nombre}: {clave} repetido"


def test_sin_nulos_en_columnas_clave(tabla):
    nombre, df = tabla
    _, obligatorias = REGLAS[nombre]
    for c in obligatorias:
        vacias = (df[c].str.strip() == "").sum()
        assert vacias == 0, f"{nombre}.{c}: {vacias} vacias"


def test_transacciones_validas():
    tx = pd.read_csv(TRATADOS / "transacciones.csv")
    usuarios = pd.read_csv(TRATADOS / "usuarios.csv")
    assert (tx["monto_usd"] > 0).all(), "montos <= 0 debieron descartarse"
    assert tx["usuario_id"].isin(usuarios["usuario_id"]).all(), "toda transaccion debe tener usuario"
    assert set(tx["canal"]) <= {"app", "web", "pos", "api"}
    assert set(tx["moneda"]) <= {"CLP", "USD"}
    assert pd.to_datetime(tx["ts_utc"], utc=True).notna().all()
    assert set(tx["es_fraude"].dropna().unique()) <= {0, 1}
    # columnas que filtran la respuesta (fuga de informacion) no deben llegar al modelo ni a la base
    assert "revisado_por_analista" not in tx.columns and "motivo_bloqueo" not in tx.columns
    # minimizacion (RN5, RNF5): ni la IP ni la tarjeta enmascarada llegan a la base
    assert "ip" not in tx.columns and "tarjeta_enmascarada" not in tx.columns


def test_categorias_normalizadas():
    usuarios = pd.read_csv(TRATADOS / "usuarios.csv")
    ln = pd.read_csv(TRATADOS / "lista_negra.csv")
    assert set(usuarios["segmento"]) <= {"persona", "comercio_pequeno", "comercio_grande", "empresa"}
    assert set(ln["tipo"]) <= {"dispositivo", "ip", "comercio", "tarjeta"}
    assert not ln.duplicated(["tipo", "valor"]).any(), "un mismo valor no puede estar dos veces en la lista negra"
