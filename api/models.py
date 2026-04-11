"""
Pydantic models para la API REST.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any


class CreateStrategyRequest(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    tipo_estrategia: str = Field(default="options_mispricing")
    activo: str = Field(default="GGAL")
    mercado: str = Field(default="bCBA")
    fondos_asignados: float = Field(default=0.0, ge=0)
    config: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = Field(default=True)


class UpdateStrategyRequest(BaseModel):
    nombre: str | None = None
    activo: str | None = None
    mercado: str | None = None
    fondos_asignados: float | None = Field(default=None, ge=0)
    config: dict[str, Any] | None = None
    dry_run: bool | None = None


class StrategyResponse(BaseModel):
    id: str
    nombre: str
    tipo_estrategia: str
    activo: str
    mercado: str
    fondos_asignados: float
    nominal_en_uso: float
    config: dict[str, Any]
    dry_run: bool
    estado: str
    created_at: str
    pnl_realizado: float
    posiciones_abiertas: list[dict]
    n_posiciones: int
    last_signals: list[dict]
    spot: float | None


class StatusResponse(BaseModel):
    connected: bool
    n_strategies: int
    n_running: int
    pnl_total: float


class ErrorResponse(BaseModel):
    detail: str
