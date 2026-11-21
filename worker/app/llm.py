"""La llamada a la API de NVIDIA: tiempo maximo de espera, reintentos con espera creciente y un camino de falla claro.

La API de NVIDIA es compatible con la de OpenAI (POST /v1/chat/completions con Authorization: Bearer): se llama con httpx,
sin cliente propio, para que cada reintento sea visible, medible y facil de explicar.

Que se reintenta y que no (EP3, 2.3 regla 5):
    429 (limite de solicitudes por minuto) y 5xx     SI, con espera creciente (2 s, 4 s, 8 s... mas un poco al azar), o la
                                                      que pida el header Retry-After
    sin respuesta en NVIDIA_TIMEOUT / sin conexion   SI
    401 (clave invalida), 404 (el modelo no existe), 410 (NVIDIA lo retiro del catalogo gratuito), 400   NO: reintentar solo
                                                      gasta cuota. Se pasa de inmediato al modo degradado
    200 pero sin texto (los modelos de razonamiento dejan content = null)   NO: se pasa al modo degradado

Al agotarse los intentos se lanza LLMNoDisponible: el worker marca la prediccion como "degradado". El modelo de SageMaker ya
decidio; el LLM solo redacta, asi que su falla nunca detiene una decision.
"""
from __future__ import annotations

import json
import os
import random
import time

import httpx

from app.anonimizar import anonimizar


class LLMNoDisponible(Exception):
    """El LLM no respondio despues de los reintentos, o respondio algo que no se arregla reintentando."""


def _config() -> dict:
    """Se lee del entorno en cada llamada: asi un test (o un cambio de .env + reinicio) toma el valor nuevo."""
    return {
        "base_url": os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/"),
        "modelo": os.getenv("NVIDIA_MODEL", ""),
        "clave": os.getenv("NVIDIA_API_KEY", ""),
        "timeout": float(os.getenv("NVIDIA_TIMEOUT", "20")),            # segundos por intento
        "intentos": int(os.getenv("NVIDIA_INTENTOS", "4")),             # intentos en total, no reintentos
        "espera_base": float(os.getenv("NVIDIA_ESPERA_BASE", "2")),
        "max_tokens": int(os.getenv("NVIDIA_MAX_TOKENS", "300")),
    }


def log(**campos) -> None:
    """Una linea JSON por evento. Nunca incluye la clave ni el texto enviado."""
    print(json.dumps({"servicio": "worker", **campos}, ensure_ascii=False), flush=True)


def modelo_configurado() -> str:
    return _config()["modelo"]


def redactar(instruccion: str, contenido: str, dormir=time.sleep, cliente: httpx.Client | None = None) -> str:
    c = _config()
    if not c["modelo"]:
        raise LLMNoDisponible("falta NVIDIA_MODEL")
    # Ultima barrera de privacidad: lo que sale hacia NVIDIA ya esta enmascarado.
    cuerpo = {"model": c["modelo"], "temperature": 0.3, "max_tokens": c["max_tokens"],
              "messages": [{"role": "system", "content": anonimizar(instruccion)},
                           {"role": "user", "content": anonimizar(contenido)}]}
    cabeceras = {"Authorization": f"Bearer {c['clave'] or 'sin-clave'}", "Content-Type": "application/json"}
    propio = cliente is None
    cliente = cliente or httpx.Client()
    try:
        for intento in range(1, c["intentos"] + 1):
            espera_pedida = None
            try:
                r = cliente.post(f"{c['base_url']}/chat/completions", json=cuerpo, headers=cabeceras, timeout=c["timeout"])
            except httpx.HTTPError as e:                       # incluye el tiempo agotado y la falta de conexion
                motivo = type(e).__name__
            else:
                if r.status_code == 200:
                    try:
                        texto = (r.json()["choices"][0]["message"]["content"] or "").strip()
                    except (ValueError, KeyError, IndexError, TypeError):
                        texto = ""
                    if not texto:
                        raise LLMNoDisponible("respuesta vacia (¿modelo de razonamiento?)")
                    return texto
                if r.status_code != 429 and r.status_code < 500:
                    raise LLMNoDisponible(f"HTTP {r.status_code}")          # 401, 404, 410, 400: no se arregla esperando
                motivo = f"HTTP {r.status_code}"
                espera_pedida = r.headers.get("retry-after")
            if intento == c["intentos"]:
                raise LLMNoDisponible(f"{motivo} despues de {c['intentos']} intentos")
            try:
                espera = float(espera_pedida) if espera_pedida else c["espera_base"] * 2 ** (intento - 1)
            except ValueError:
                espera = c["espera_base"] * 2 ** (intento - 1)
            espera += random.uniform(0, espera / 2)            # jitter: que no reintenten todos en el mismo segundo
            espera = min(espera, 30)
            log(evento="reintento", motivo=motivo, intento=intento, espera_s=round(espera, 1))
            dormir(espera)
    finally:
        if propio:
            cliente.close()
    raise LLMNoDisponible("sin intentos")                      # no se alcanza: el ultimo intento lanza antes
