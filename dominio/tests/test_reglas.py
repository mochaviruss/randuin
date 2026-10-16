"""Tests de las reglas de negocio (no necesitan base ni red)."""
from datetime import datetime, timezone

from app import reglas


def test_monto_en_clp_se_pasa_a_usd():
    assert reglas.a_usd(95_000, "CLP") == 100.0
    assert reglas.a_usd(12.5, "USD") == 12.5


def test_decision_segun_umbrales_de_riesgo():
    assert reglas.decidir(0.10, 0.30, 0.70) == "aprobar"
    assert reglas.decidir(0.30, 0.30, 0.70) == "revisar"
    assert reglas.decidir(0.95, 0.30, 0.70) == "bloquear"


def test_variables_iguales_al_tratamiento():
    fecha = datetime(2026, 10, 11, 6, 30, tzinfo=timezone.utc)          # 03:30 en Chile
    v = reglas.variables_modelo(monto_usd=52.0, fecha=fecha, intentos_previos_1h=2, dispositivo_id=None,
                                usuarios_en_dispositivo=None, pais_transaccion="AR", canal="web")
    assert v == {"monto_usd": 52.0, "hora_local": 3, "intentos_previos_1h": 2, "dispositivo_usuarios_distintos": -1,
                 "sin_dispositivo": True, "pais_extranjero": True, "canal": "web"}


def test_fecha_sin_zona_es_hora_de_chile():
    assert reglas.fecha_utc(datetime(2026, 1, 15, 12, 0)).hour == 15          # enero: Chile es UTC-3


def test_reserva_aprueba_salvo_senal_fuerte():
    base = {"intentos_previos_1h": 0, "dispositivo_usuarios_distintos": 1}
    assert reglas.reserva(base, "timeout")[0] == "aprobar"
    assert reglas.reserva({**base, "intentos_previos_1h": 5}, "timeout")[0] == "revisar"
    assert reglas.reserva({**base, "dispositivo_usuarios_distintos": 8}, "timeout")[0] == "revisar"
