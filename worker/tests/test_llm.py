"""La llamada a NVIDIA con httpx simulado: reintentos con espera creciente ante el 429, que NO se reintenta, y que lo que sale ya
esta enmascarado. No hay red ni clave: un MockTransport hace de API de NVIDIA."""
import json

import httpx
import pytest

from app import llm


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    monkeypatch.setenv("NVIDIA_MODEL", "modelo-de-prueba")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-clave-de-prueba")
    monkeypatch.setenv("NVIDIA_INTENTOS", "4")
    monkeypatch.setenv("NVIDIA_ESPERA_BASE", "2")


def api(*respuestas):
    """Una API que responde, en orden, cada elemento (un codigo, o (codigo, headers), o una excepcion). Guarda lo que recibio."""
    recibidos, pendientes = [], list(respuestas)

    def manejador(peticion: httpx.Request) -> httpx.Response:
        recibidos.append(peticion)
        r = pendientes.pop(0) if len(pendientes) > 1 else pendientes[0]
        if isinstance(r, Exception):
            raise r
        if isinstance(r, httpx.Response):
            return r
        codigo, cabeceras = (r, {}) if isinstance(r, int) else r
        return httpx.Response(codigo, headers=cabeceras, json={"error": "simulado"})

    return httpx.Client(transport=httpx.MockTransport(manejador)), recibidos


def ok(texto="Se revisa por intentos repetidos."):
    return httpx.Response(200, json={"choices": [{"message": {"content": texto}}]})


def test_respuesta_normal_devuelve_el_texto():
    cliente, recibidos = api(ok("Texto listo."))
    assert llm.redactar("redacta", "contenido", cliente=cliente) == "Texto listo."
    assert len(recibidos) == 1
    assert recibidos[0].headers["authorization"] == "Bearer nvapi-clave-de-prueba"
    assert json.loads(recibidos[0].content)["model"] == "modelo-de-prueba"


def test_ante_un_429_reintenta_con_espera_creciente():
    esperas = []
    cliente, recibidos = api(429, 429, ok())
    assert llm.redactar("i", "c", dormir=esperas.append, cliente=cliente) == "Se revisa por intentos repetidos."
    assert len(recibidos) == 3
    assert len(esperas) == 2 and esperas[1] > esperas[0]               # 2 s (+ azar) y despues 4 s (+ azar)
    assert 2 <= esperas[0] <= 3 and 4 <= esperas[1] <= 6


def test_respeta_retry_after_si_la_api_lo_pide():
    esperas = []
    cliente, _ = api((429, {"retry-after": "7"}), ok())
    llm.redactar("i", "c", dormir=esperas.append, cliente=cliente)
    assert 7 <= esperas[0] <= 10.5


def test_agotados_los_intentos_lanza_llm_no_disponible():
    esperas = []
    cliente, recibidos = api(429)
    with pytest.raises(llm.LLMNoDisponible, match="HTTP 429 despues de 4 intentos"):
        llm.redactar("i", "c", dormir=esperas.append, cliente=cliente)
    assert len(recibidos) == 4 and len(esperas) == 3


@pytest.mark.parametrize("codigo", [401, 404, 410, 400])
def test_clave_invalida_modelo_inexistente_o_retirado_no_se_reintentan(codigo):
    """Reintentar un 401 solo gasta cuota: se pasa al modo degradado de inmediato."""
    cliente, recibidos = api(codigo)
    with pytest.raises(llm.LLMNoDisponible, match=f"HTTP {codigo}"):
        llm.redactar("i", "c", dormir=lambda s: None, cliente=cliente)
    assert len(recibidos) == 1


def test_un_5xx_si_se_reintenta():
    cliente, recibidos = api(503, ok())
    assert llm.redactar("i", "c", dormir=lambda s: None, cliente=cliente)
    assert len(recibidos) == 2


def test_la_api_caida_o_sin_respuesta_se_reintenta_y_termina_en_no_disponible():
    cliente, recibidos = api(httpx.ConnectTimeout("sin respuesta"))
    with pytest.raises(llm.LLMNoDisponible, match="ConnectTimeout despues de 4 intentos"):
        llm.redactar("i", "c", dormir=lambda s: None, cliente=cliente)
    assert len(recibidos) == 4


def test_un_200_sin_texto_es_una_falla_no_un_resumen_vacio():
    """Los modelos de razonamiento responden 200 con content = null."""
    cliente, recibidos = api(httpx.Response(200, json={"choices": [{"message": {"content": None}}]}))
    with pytest.raises(llm.LLMNoDisponible, match="vacia"):
        llm.redactar("i", "c", cliente=cliente)
    assert len(recibidos) == 1


def test_sin_modelo_configurado_ni_siquiera_llama():
    import os
    os.environ.pop("NVIDIA_MODEL")
    cliente, recibidos = api(ok())
    with pytest.raises(llm.LLMNoDisponible, match="NVIDIA_MODEL"):
        llm.redactar("i", "c", cliente=cliente)
    assert recibidos == []


def test_lo_que_sale_hacia_nvidia_ya_esta_anonimizado():
    cliente, recibidos = api(ok())
    llm.redactar("Redacta para el analista", "RUT 12.345.678-9, correo ana@correo.cl, tarjeta 4111 1111 1111 1111", cliente=cliente)
    enviado = recibidos[0].content.decode()
    for dato in ("12.345.678-9", "ana@correo.cl", "4111 1111 1111 1111"):
        assert dato not in enviado


def test_la_clave_no_aparece_en_los_logs(capsys):
    cliente, _ = api(429, ok())
    llm.redactar("i", "c", dormir=lambda s: None, cliente=cliente)
    assert "nvapi-clave-de-prueba" not in capsys.readouterr().out
