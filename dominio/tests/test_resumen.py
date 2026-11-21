"""El pedido que se le manda al LLM: que lleva y, sobre todo, que NO lleva. No necesita base de datos."""
from app import resumen

RAZONES = [{"variable": "intentos_previos_1h", "condicion": "intentos del usuario en la ultima hora = 5 (sube el riesgo)"},
           {"variable": "monto_usd", "condicion": "monto (USD) = 52 (sube el riesgo)"}]


def pedido(**cambios):
    datos = dict(transaccion_id="TX-ABC", decision="bloquear", origen="modelo", razones=RAZONES, monto_usd=52.4, canal="web", hora_local=3)
    return resumen.armar_pedido(**{**datos, **cambios})


def test_lleva_decision_nivel_razones_monto_canal_y_hora():
    p = pedido()
    assert p["id"] == "TX-ABC" and p["instruccion"] == resumen.INSTRUCCION
    c = p["contenido"]
    for esperado in ("Decisión del sistema: bloquear", "Nivel de riesgo: alto", "intentos del usuario en la ultima hora = 5",
                     "monto (USD) = 52", "USD 52", "Canal: web", "Hora local: 03:00"):
        assert esperado in c, esperado


def test_el_nivel_depende_de_quien_decidio():
    assert "alto" in pedido(decision="bloquear")["contenido"]
    assert "Nivel de riesgo: medio" in pedido(decision="revisar")["contenido"]
    assert "regla dura" in pedido(origen="lista_negra")["contenido"]
    assert "sin evaluar por el modelo" in pedido(origen="reserva", decision="revisar")["contenido"]


def test_no_lleva_la_probabilidad_cruda_ni_identidades():
    """La funcion ni siquiera recibe el score ni el usuario, el dispositivo o el comercio: no pueden filtrarse."""
    import inspect
    parametros = set(inspect.signature(resumen.armar_pedido).parameters)
    assert not parametros & {"score", "usuario_id", "dispositivo_id", "comercio_id", "tarjeta"}


def test_la_instruccion_le_prohibe_citar_probabilidades_e_identidades():
    i = resumen.INSTRUCCION
    assert "No inventes datos" in i and "no cites probabilidades" in i and "no menciones identidades" in i


def test_se_limita_a_cinco_razones():
    muchas = [{"condicion": f"razon {n}"} for n in range(9)]
    assert pedido(razones=muchas)["contenido"].count("\n- ") == 5
