"""Arma el pedido que el worker le manda al LLM para redactar el resumen del analista (RF5, RNF6 del caso).

Que va y que NO va (EP3, 2.3 regla 6 y RNF5):
  VA     la decision, el origen, un NIVEL de riesgo, las razones del modelo en palabras, el monto, el canal y la hora.
  NO VA  el identificador del usuario, del dispositivo o del comercio, ni el numero de tarjeta (que ni siquiera entra al sistema).
  NO VA  la probabilidad cruda. Decision de diseno: la probabilidad sale de una muestra con ~15 % de fraude y en produccion es
         0,012 % (R3 del caso); si el LLM la repite ("alta probabilidad de 0,91") un analista puede leerla como una chance real.
         El numero sigue visible en la pantalla del dominio; al LLM se le da el nivel.
El worker aplica ademas su propia anonimizacion antes de salir hacia NVIDIA: esta es la primera barrera, esa es la ultima.
"""
from __future__ import annotations

INSTRUCCION = (
    "Eres un asistente para analistas de fraude de un procesador de pagos en Chile. Redacta en español, en 2 o 3 frases claras y "
    "sin tecnicismos, por qué la transacción quedó en la decisión indicada, usando SOLO los datos entregados. "
    "No inventes datos, no cites probabilidades ni porcentajes, no pidas más información y no menciones identidades de personas. "
    "Si la decisión fue por una regla dura o por falta del modelo, dilo con claridad."
)

ORIGEN_EN_PALABRAS = {
    "modelo": "el modelo de detección de fraude",
    "lista_negra": "una regla dura: el dispositivo o el comercio está en la lista negra (el modelo no se consultó)",
    "reserva": "la política de reserva, porque el modelo no respondió a tiempo",
}


def nivel_de_riesgo(decision: str, origen: str) -> str:
    if origen == "lista_negra":
        return "máximo (regla dura)"
    if origen == "reserva":
        return "sin evaluar por el modelo"
    return "alto" if decision == "bloquear" else "medio"


def armar_pedido(*, transaccion_id: str, decision: str, origen: str, razones: list[dict], monto_usd: float, canal: str,
                 hora_local: int) -> dict:
    lineas = [f"Decisión del sistema: {decision}.",
              f"La tomó: {ORIGEN_EN_PALABRAS.get(origen, origen)}.",
              f"Nivel de riesgo: {nivel_de_riesgo(decision, origen)}.",
              f"Monto: USD {monto_usd:.0f}. Canal: {canal}. Hora local: {hora_local:02d}:00.",
              "Razones, de mayor a menor peso:"]
    lineas += [f"- {r.get('condicion', '')}" for r in razones[:5]]
    return {"id": transaccion_id, "instruccion": INSTRUCCION, "contenido": "\n".join(lineas)}
