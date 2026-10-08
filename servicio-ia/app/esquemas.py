"""Contratos de entrada y salida del Motor de Inferencia (Pydantic). Lo que no cumpla -> 422."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Transaccion(BaseModel):
    """Las 7 variables que arma la API de Decision (dominio/app/logica.py:variables_modelo)."""
    model_config = ConfigDict(extra="forbid")

    monto_usd: float = Field(..., gt=0, le=1_000_000, examples=[180.0])
    hora_local: int = Field(..., ge=0, le=23, examples=[3])
    intentos_previos_1h: int = Field(0, ge=0, le=1000, examples=[4])
    dispositivo_usuarios_distintos: int = Field(-1, ge=-1, le=10_000, description="-1 si el dispositivo no se conoce")
    sin_dispositivo: bool = False
    pais_extranjero: bool = False
    canal: Literal["app", "web", "pos", "api"] = "app"
    transaccion_id: str | None = Field(None, max_length=64, description="solo para trazar, no entra al modelo")

    def a_fila(self) -> dict:
        """Mismas columnas y tipos que datos/salida/etiquetadas.csv (lo que vio el entrenamiento)."""
        fila = self.model_dump(exclude={"transaccion_id"})
        fila["sin_dispositivo"] = int(self.sin_dispositivo)
        fila["pais_extranjero"] = int(self.pais_extranjero)
        return fila


EJEMPLO = {"monto_usd": 52.0, "hora_local": 3, "intentos_previos_1h": 5, "dispositivo_usuarios_distintos": 4,
           "sin_dispositivo": False, "pais_extranjero": True, "canal": "web", "transaccion_id": "TX-EJEMPLO"}


class Razon(BaseModel):
    variable: str
    valor: float | str
    efecto: Literal["sube", "baja"]
    aporte: float          # cuanto mueve el riesgo (log-odds) frente al valor tipico: positivo sube, negativo baja
    condicion: str         # en palabras, para el analista o un auditor no tecnico (RT8)


class Prediccion(BaseModel):
    transaccion_id: str | None
    score: float                         # probabilidad de fraude, 0 a 1
    prediccion: Literal["fraude", "legitima"]
    nivel_riesgo: Literal["bajo", "medio", "alto"]
    umbral: float                        # el de la metadata; la decision final la toma el dominio con sus umbrales
    razones: list[Razon]                 # las 3 de mayor peso (RF2)
    aportes: list[Razon]                 # todas, para auditoria (RF7)
    modelo_version: str
    modelo_hash: str
    replica: str
    latencia_ms: float


class Lote(BaseModel):
    transacciones: list[Transaccion] = Field(..., min_length=1, max_length=500)


class RespuestaLote(BaseModel):
    modelo_version: str
    total: int
    alertas: int
    latencia_ms: float
    predicciones: list[Prediccion]
