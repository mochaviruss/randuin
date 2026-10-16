"""Reglas de negocio del dominio. Son funciones simples, sin red ni base: se prueban solas (tests/test_reglas.py)."""
from datetime import datetime
from zoneinfo import ZoneInfo

CHILE = ZoneInfo("America/Santiago")
CLP_POR_USD = 950      # el mismo tipo de cambio que usa el tratamiento (datos/preparar.py)

# Politica de reserva (RNF3): si el modelo no responde, se decide con dos reglas simples.
# Son las dos senales mas fuertes del fraude etiquetado (8.4).
RESERVA_INTENTOS = 4
RESERVA_USUARIOS_DISPOSITIVO = 5


def a_usd(monto: float, moneda: str) -> float:
    return round(monto / CLP_POR_USD if moneda == "CLP" else monto, 2)


def fecha_utc(fecha: datetime | None) -> datetime:
    """Sin fecha = ahora. Una fecha sin zona horaria se entiende en hora de Chile (igual que en el tratamiento)."""
    if fecha is None:
        return datetime.now(ZoneInfo("UTC"))
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=CHILE)
    return fecha.astimezone(ZoneInfo("UTC"))


def variables_modelo(*, monto_usd: float, fecha: datetime, intentos_previos_1h: int, dispositivo_id: str | None,
                     usuarios_en_dispositivo: int | None, pais_transaccion: str, canal: str) -> dict:
    """La entrada de POST /predict, calculada con las MISMAS reglas que datos/preparar.py:calcular_variables."""
    return {
        "monto_usd": monto_usd,
        "hora_local": fecha.astimezone(CHILE).hour,
        "intentos_previos_1h": intentos_previos_1h,
        "dispositivo_usuarios_distintos": usuarios_en_dispositivo if usuarios_en_dispositivo is not None else -1,
        "sin_dispositivo": dispositivo_id is None,
        "pais_extranjero": pais_transaccion != "CL",
        "canal": canal,
    }


def decidir(score: float, umbral_revisar: float, umbral_bloquear: float) -> str:
    """Los umbrales los fija Riesgo en la tabla umbrales (RF3), no el modelo."""
    if score >= umbral_bloquear:
        return "bloquear"
    if score >= umbral_revisar:
        return "revisar"
    return "aprobar"


def reserva(variables: dict, motivo: str) -> tuple[str, list[dict]]:
    """Que se decide si el modelo no responde a tiempo (RNF3): se aprueba, salvo que una regla vea una senal fuerte."""
    razones = [{"regla": "reserva", "condicion": f"el modelo no respondio ({motivo}); se aplica la politica de reserva"}]
    if variables["intentos_previos_1h"] >= RESERVA_INTENTOS:
        razones.append({"regla": "reserva", "condicion": f"{variables['intentos_previos_1h']} intentos en la ultima hora"})
    if variables["dispositivo_usuarios_distintos"] >= RESERVA_USUARIOS_DISPOSITIVO:
        razones.append({"regla": "reserva",
                        "condicion": f"dispositivo usado por {variables['dispositivo_usuarios_distintos']} usuarios"})
    return ("revisar" if len(razones) > 1 else "aprobar"), razones
