"""Quita los datos personales de un texto ANTES de que salga hacia NVIDIA (EP3, 2.3 regla 6 y RNF5 del caso).

Se aplica en el worker, que es el ultimo paso antes de la API externa: asi, pase lo que pase aguas arriba, lo que sale ya
esta enmascarado. Enmascara RUT, correo, telefono, numero de tarjeta, IP y direcciones con calle y numero. Los nombres
de personas no se pueden detectar con una expresion regular: se enmascaran solo los que se le pasan en `nombres`.

La defensa de fondo es otra: al LLM nunca se le manda la identidad del usuario. El dominio arma el texto solo con la
decision, la probabilidad, las razones del modelo, el monto, el canal y la hora. Esta funcion es la segunda barrera.

El orden importa: el correo va primero. Si la tarjeta o el telefono corrieran antes, ana1234567890123@correo.cl quedaria
"ana[TARJETA]@correo.cl" con el dominio a la vista.
"""
from __future__ import annotations

import re
from typing import Iterable

PATRONES = [
    ("[CORREO]", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    # IPv4: 72.152.180.159
    # (no puede seguir otro digito ni ".digito": un punto de fin de frase despues del IP no cuenta)
    ("[IP]", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d)(?!\.\d)")),
    # tarjeta: 13 a 19 digitos, con o sin espacios o guiones entre grupos
    ("[TARJETA]", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")),
    # RUT: 12.345.678-9, 12345678-9, 9.876.543-K
    ("[RUT]", re.compile(r"(?<![\d.])\d{1,2}(?:\.?\d{3}){2}\s?-\s?[\dkK](?![\w])")),
    # telefono chileno: +56 9 1234 5678, 56912345678, 912345678, (2) 2345 6789
    ("[TELEFONO]", re.compile(r"(?<!\d)(?:\+?56[ -]?)?\(?[2-9]\)?[ -]?\d{4}[ -]?\d{4}(?!\d)")),
    # direccion: "Avenida Providencia 1234", "calle Los Olmos 45", "Pasaje Sur N° 12"
    ("[DIRECCION]", re.compile(r"\b(?:calle|avenida|av\.?|pasaje|psje\.?|camino)\s+[^\d\n,;]{1,40}?\s*(?:n[°ºo.]*\s*)?\d{1,5}\b",
                               re.IGNORECASE)),
]


def anonimizar(texto: str, nombres: Iterable[str] = ()) -> str:
    """Devuelve el texto sin RUT, correo, telefono, tarjeta, IP ni direcciones; y sin los `nombres` indicados."""
    for reemplazo, patron in PATRONES:
        texto = patron.sub(reemplazo, texto)
    for nombre in nombres:
        if nombre and nombre.strip():
            texto = re.sub(re.escape(nombre.strip()), "[NOMBRE]", texto, flags=re.IGNORECASE)
    return texto
