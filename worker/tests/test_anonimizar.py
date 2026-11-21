"""Prueba 5 de EP3 (4.3): un texto con RUT, correo, telefono y tarjeta pasa por la funcion de anonimizacion y NINGUNO
de los cuatro sobrevive. Mas los bordes que suelen romperla."""
import pytest

from app.anonimizar import anonimizar

TEXTO = ("Cliente Juan Perez, RUT 12.345.678-9, correo juan.perez@correo.cl, telefono +56 9 8765 4321, "
         "tarjeta 4111 1111 1111 1111, vive en Avenida Providencia 1234. Compro desde 72.152.180.159.")


def test_ni_el_rut_ni_el_correo_ni_el_telefono_ni_la_tarjeta_sobreviven():
    salida = anonimizar(TEXTO)
    for dato in ("12.345.678-9", "juan.perez@correo.cl", "8765 4321", "4111 1111 1111 1111"):
        assert dato not in salida, f"{dato} sobrevivio: {salida}"
    for marca in ("[RUT]", "[CORREO]", "[TELEFONO]", "[TARJETA]"):
        assert marca in salida


def test_tambien_enmascara_direccion_e_ip_y_los_nombres_que_se_le_indican():
    salida = anonimizar(TEXTO, nombres=["Juan Perez"])
    assert "Providencia" not in salida and "72.152.180.159" not in salida and "Juan Perez" not in salida
    assert "[DIRECCION]" in salida and "[IP]" in salida and "[NOMBRE]" in salida


@pytest.mark.parametrize("rut", ["12.345.678-9", "12345678-9", "9.876.543-K", "9876543-k", "1.234.567 - 8"])
def test_formas_de_escribir_un_rut(rut):
    assert rut not in anonimizar(f"el usuario {rut} pidio una devolucion")


@pytest.mark.parametrize("tarjeta", ["4111111111111111", "4111 1111 1111 1111", "4111-1111-1111-1111", "5500 0000 0000 0004",
                                     "3782 822463 10005"])
def test_formas_de_escribir_una_tarjeta(tarjeta):
    assert "[TARJETA]" in anonimizar(f"cobro con la tarjeta {tarjeta} rechazado")


@pytest.mark.parametrize("telefono", ["+56 9 8765 4321", "56987654321", "987654321", "(2) 2345 6789"])
def test_formas_de_escribir_un_telefono(telefono):
    assert "[TELEFONO]" in anonimizar(f"llamar al {telefono}")


def test_el_correo_va_primero_y_no_deja_el_dominio_a_la_vista():
    """Si la tarjeta corriera antes, ana1234567890123@correo.cl quedaria 'ana[TARJETA]@correo.cl'."""
    assert anonimizar("ana1234567890123@correo.cl") == "[CORREO]"


def test_el_texto_de_una_decision_normal_no_se_toca():
    """No hay falsos positivos sobre lo que de verdad se manda: decision, probabilidad, razones, monto, canal y hora."""
    texto = ("Decision: revisar. Probabilidad de fraude 0,64. Razones: intentos del usuario en la ultima hora = 5 (sube el "
             "riesgo); monto (USD) = 52 (sube el riesgo); pais distinto de Chile = si. Canal: web. Hora local: 03.")
    assert anonimizar(texto) == texto


def test_un_texto_vacio_o_sin_datos_pasa_igual():
    assert anonimizar("") == "" and anonimizar("sin datos personales") == "sin datos personales"
