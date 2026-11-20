"""El worker: que hace con un pedido segun lo que responda el LLM y el dominio. El dominio se simula con un MockTransport (guarda
lo que le entregan) y el LLM con una funcion: no hay RabbitMQ, red ni claves.

Prueba 6 de EP3 (4.3), en el worker: con el LLM en 429 o caido, la prediccion no se toca y el estado queda en modo degradado."""
import httpx
import pytest

from app import llm, worker

PEDIDO = {"id": "TX-ABC123", "instruccion": "redacta", "contenido": "Decision: bloquear. Probabilidad 0,91."}


class Dominio:
    """Hace de la API interna del dominio: GET y PUT /interno/predicciones/{id}/llm."""

    def __init__(self, estado="pendiente", existe=True, caido=False):
        self.estado, self.existe, self.caido, self.entregados = estado, existe, caido, []
        self.cliente = httpx.Client(transport=httpx.MockTransport(self.manejar))

    def manejar(self, peticion: httpx.Request) -> httpx.Response:
        if self.caido:
            raise httpx.ConnectError("dominio caido")
        if not self.existe:
            return httpx.Response(404)
        if peticion.method == "GET":
            return httpx.Response(200, json={"estado": self.estado})
        import json
        datos = json.loads(peticion.content)
        self.entregados.append(datos)
        self.estado = datos["estado"]
        return httpx.Response(200, json={"ok": True})


def texto_listo(instruccion, contenido):
    return "Se bloqueo por intentos repetidos y un dispositivo compartido."


def llm_caido(motivo):
    def f(instruccion, contenido):
        raise llm.LLMNoDisponible(motivo)
    return f


def test_pedido_normal_entrega_el_texto_y_queda_listo():
    d = Dominio()
    assert worker.procesar(PEDIDO, redactar=texto_listo, cliente=d.cliente) == "listo"
    assert d.entregados[0]["estado"] == "listo" and "intentos repetidos" in d.entregados[0]["texto"]
    assert d.entregados[0]["latencia_ms"] >= 0


@pytest.mark.parametrize("motivo", ["HTTP 429 despues de 4 intentos", "ConnectTimeout despues de 4 intentos", "HTTP 401"])
def test_con_el_llm_en_429_caido_o_con_clave_invalida_el_estado_queda_degradado(motivo):
    """El worker no lanza: avisa al dominio 'degradado' con el motivo y sigue. La prediccion ya estaba respondida."""
    d = Dominio()
    assert worker.procesar(PEDIDO, redactar=llm_caido(motivo), cliente=d.cliente) == "degradado"
    assert d.entregados == [{"estado": "degradado", "motivo": motivo}]


def test_idempotente_si_el_texto_ya_esta_listo_no_vuelve_a_llamar_al_llm():
    """Si el worker murio entre entregar y el ack, RabbitMQ entrega el mismo mensaje otra vez."""
    d = Dominio(estado="listo")
    llamadas = []
    assert worker.procesar(PEDIDO, redactar=lambda i, c: llamadas.append(1), cliente=d.cliente) == "listo"
    assert llamadas == [] and d.entregados == []


def test_si_la_prediccion_ya_no_existe_se_descarta_sin_llamar_al_llm():
    d = Dominio(existe=False)
    llamadas = []
    assert worker.procesar(PEDIDO, redactar=lambda i, c: llamadas.append(1), cliente=d.cliente) == "descartado"
    assert llamadas == []


def test_si_el_dominio_no_responde_lanza_para_que_el_mensaje_vuelva_a_la_cola():
    d = Dominio(caido=True)
    with pytest.raises(httpx.HTTPError):
        worker.procesar(PEDIDO, redactar=texto_listo, cliente=d.cliente)


def test_el_ritmo_deja_al_menos_el_intervalo_entre_llamadas():
    """El limite de NVIDIA (~40 por minuto) es parte del problema: 1,6 s entre llamadas son 37 por minuto."""
    tiempo, dormidas = [100.0], []

    def dormir(s):
        dormidas.append(round(s, 2))
        tiempo[0] += s

    ritmo = worker.Ritmo(intervalo=1.6, reloj=lambda: tiempo[0], dormir=dormir)
    assert ritmo.esperar() == 0                      # la primera no espera
    tiempo[0] += 0.5                                 # la llamada anterior tardo 0,5 s
    assert ritmo.esperar() == pytest.approx(1.1)     # faltaba 1,1 s para completar los 1,6
    tiempo[0] += 3.0                                 # un rato sin mensajes
    assert ritmo.esperar() == 0
    assert dormidas == [1.1]
