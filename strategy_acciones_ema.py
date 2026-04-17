"""
Módulo: Estrategia Acciones EMA + RSI (Acciones Argentinas)

Estrategia intradiaria que opera DIRECTAMENTE sobre acciones (no opciones):
  - Tendencia alcista (EMA golden cross)  → COMPRA la acción
  - Tendencia bajista (EMA death cross)   → VENDE la posición abierta
  - Stop-loss y Take-profit sobre precio de compra

Modos de dimensionamiento:
  - "cantidad": compra N acciones fijas por trade
  - "monto":    compra tantas acciones como cubra el monto ARS configurado

Solo opera en mercado bCBA (acciones argentinas).
Solo posiciones LONG (no venta en descubierto).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oms import OMS

from market_data import StockSnapshot

logger = logging.getLogger(__name__)


# ─── Configuración ────────────────────────────────────────────────────────────

@dataclass
class AccionesEMAConfig:
    """Parámetros de la estrategia Acciones EMA + RSI."""

    # ── Indicadores técnicos ─────────────────────────────────────────────
    ema_rapida: int = 9
    """Períodos para la EMA rápida (~30s por snapshot)."""

    ema_lenta: int = 21
    """Períodos para la EMA lenta. 21 snapshots ≈ 10.5 min de warmup."""

    rsi_periodos: int = 14
    """Períodos para el cálculo de RSI."""

    rsi_overbought: int = 70
    """No comprar si RSI supera este nivel (sobrecompra)."""

    rsi_oversold: int = 30
    """No comprar si RSI cae debajo de este nivel (sobreventa extrema)."""

    # ── Dimensionamiento ─────────────────────────────────────────────────
    modo_cantidad: str = "cantidad"
    """'cantidad' = N acciones fijas | 'monto' = monto ARS fijo."""

    cantidad_acciones: int = 1
    """Acciones a comprar por trade. Usado si modo_cantidad='cantidad'."""

    monto_por_trade: float = 50000.0
    """Monto ARS por trade. Usado si modo_cantidad='monto'."""

    # ── Gestión de riesgo ────────────────────────────────────────────────
    stop_loss_pct: float = 0.05
    """Vender si el precio cae X% de lo pagado. Ej: 0.05 = -5%."""

    take_profit_pct: float = 0.10
    """Vender si el precio sube X% sobre lo pagado. Ej: 0.10 = +10%."""

    # ── Límites ──────────────────────────────────────────────────────────
    max_posiciones: int = 1
    """Máximo de posiciones abiertas simultáneas."""

    cooldown_snapshots: int = 3
    """Snapshots de espera después de cerrar antes de abrir otra."""

    # ── Control EOD ──────────────────────────────────────────────────────
    force_close_eod: bool = True
    """Cerrar todo a las 16:45 ARG para evitar quedar posicionado overnight."""


# ─── Señal ────────────────────────────────────────────────────────────────────

@dataclass
class AccionesSignal:
    """Señal generada por los indicadores técnicos."""
    simbolo:       str
    precio_limite: float
    cantidad:      int
    ema_fast:      float
    ema_slow:      float
    rsi:           float
    razon:         str
    score:         float


# ─── Estrategia ───────────────────────────────────────────────────────────────

class AccionesEMAStrategy:
    """
    Motor de daytrading EMA + RSI sobre acciones argentinas.

    Compra acciones en Golden Cross (EMA rápida cruza hacia arriba de la lenta).
    Vende en Death Cross, Stop-Loss o Take-Profit.
    Nunca vende en descubierto — solo LONG.
    """

    def __init__(self, oms: "OMS", config: AccionesEMAConfig | None = None) -> None:
        self._oms = oms
        self._config = config or AccionesEMAConfig()

        # Historial de precios acumulados
        self._price_history: list[float] = []

        # Estado EMA anterior para detectar cruces
        self._prev_ema_fast: float | None = None
        self._prev_ema_slow: float | None = None

        # Cooldown post-operación
        self._cooldown_counter: int = 0

        # Para serialización UI
        self._last_signal: AccionesSignal | None = None
        self._last_indicators: dict = {}

    # ── Indicadores ──────────────────────────────────────────────────────

    @staticmethod
    def _calc_ema(prices: list[float], period: int) -> float:
        """EMA con seed SMA. Si no hay suficientes datos, retorna SMA."""
        if len(prices) < period:
            return sum(prices) / len(prices)
        multiplier = 2.0 / (period + 1)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    @staticmethod
    def _calc_rsi(prices: list[float], period: int) -> float:
        """RSI entre 0 y 100. Retorna 50 si no hay suficientes datos."""
        if len(prices) < period + 1:
            return 50.0
        changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        recent = changes[-period:]
        gains  = [c for c in recent if c > 0]
        losses = [-c for c in recent if c < 0]
        avg_gain = sum(gains) / period if gains else 0.0
        avg_loss = sum(losses) / period if losses else 0.0
        if avg_loss == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    # ── Dimensionamiento ─────────────────────────────────────────────────

    def _calcular_cantidad(self, precio: float) -> int:
        """Determina cuántas acciones comprar según el modo configurado."""
        cfg = self._config
        if cfg.modo_cantidad == "monto":
            if precio <= 0:
                return 0
            return max(1, math.floor(cfg.monto_por_trade / precio))
        return cfg.cantidad_acciones

    # ── API pública ───────────────────────────────────────────────────────

    async def on_snapshot(self, snapshot: StockSnapshot) -> None:
        """
        Callback del StockDataFeed. En cada tick:
        1. Acumula precio en historial
        2. Calcula EMA + RSI
        3. Monitorea posiciones abiertas (SL/TP)
        4. Detecta cruce EMA → entrada o salida
        """
        precio = snapshot.precio
        if precio is None or precio <= 0:
            return

        # 1. Acumular
        self._price_history.append(precio)
        if len(self._price_history) > 500:
            self._price_history = self._price_history[-500:]

        cfg = self._config
        min_data = max(cfg.ema_lenta, cfg.rsi_periodos + 1)

        # 2. Warmup
        if len(self._price_history) < min_data:
            self._last_indicators = {}
            return

        # 3. Calcular indicadores
        ema_fast = self._calc_ema(self._price_history, cfg.ema_rapida)
        ema_slow = self._calc_ema(self._price_history, cfg.ema_lenta)
        rsi      = self._calc_rsi(self._price_history, cfg.rsi_periodos)

        self._last_indicators = {
            "ema_fast":    round(ema_fast, 2),
            "ema_slow":    round(ema_slow, 2),
            "rsi":         round(rsi, 1),
            "precio":      precio,
            "data_points": len(self._price_history),
        }

        logger.info(
            "📊 Acciones %s: precio=%.2f | EMA%d=%.2f EMA%d=%.2f diff=%+.2f | RSI=%.1f",
            snapshot.simbolo, precio,
            cfg.ema_rapida, ema_fast, cfg.ema_lenta, ema_slow,
            ema_fast - ema_slow, rsi,
        )

        # 4. Monitorear posiciones abiertas (SL/TP)
        await self._monitorear_posiciones(precio)

        # 5. Cooldown post-operación
        if self._cooldown_counter > 0:
            self._cooldown_counter -= 1
            return

        # 6. Detectar cruce EMA
        if self._prev_ema_fast is not None and self._prev_ema_slow is not None:
            prev_diff = self._prev_ema_fast - self._prev_ema_slow
            curr_diff = ema_fast - ema_slow

            # Golden Cross: EMA rápida cruza hacia arriba
            if prev_diff <= 0 and curr_diff > 0:
                logger.info(
                    "✨ GOLDEN CROSS %s: EMA%d=%.2f > EMA%d=%.2f RSI=%.1f",
                    snapshot.simbolo, cfg.ema_rapida, ema_fast,
                    cfg.ema_lenta, ema_slow, rsi,
                )
                if rsi < cfg.rsi_overbought:
                    await self._intentar_compra(
                        snapshot.simbolo, precio,
                        ema_fast, ema_slow, rsi,
                    )
                else:
                    logger.info(
                        "🚫 Golden Cross BLOQUEADO por RSI=%.1f >= %d (sobrecompra)",
                        rsi, cfg.rsi_overbought,
                    )

            # Death Cross: EMA rápida cruza hacia abajo → cerrar posición si hay
            elif prev_diff >= 0 and curr_diff < 0:
                logger.info(
                    "📉 DEATH CROSS %s: EMA%d=%.2f < EMA%d=%.2f RSI=%.1f — cerrando posición",
                    snapshot.simbolo, cfg.ema_rapida, ema_fast,
                    cfg.ema_lenta, ema_slow, rsi,
                )
                await self._cerrar_todo(precio, razon="Death Cross EMA")

        self._prev_ema_fast = ema_fast
        self._prev_ema_slow = ema_slow

    # ── Entrada ───────────────────────────────────────────────────────────

    async def _intentar_compra(
        self,
        simbolo:  str,
        precio:   float,
        ema_fast: float,
        ema_slow: float,
        rsi:      float,
    ) -> None:
        """Intenta abrir una posición comprando acciones."""
        cfg = self._config

        abiertas = self._oms.posiciones_abiertas()
        if len(abiertas) >= cfg.max_posiciones:
            logger.info(
                "Acciones EMA: límite de posiciones (%d/%d), ignorando señal.",
                len(abiertas), cfg.max_posiciones,
            )
            return

        cantidad = self._calcular_cantidad(precio)
        if cantidad <= 0:
            logger.warning("Acciones EMA: cantidad calculada = 0. Revisar config.")
            return

        monto_est = precio * cantidad
        modo_info = (
            f"{cantidad} acc" if cfg.modo_cantidad == "cantidad"
            else f"${monto_est:,.0f} ARS → {cantidad} acc"
        )

        razon = (
            f"Golden Cross EMA{cfg.ema_rapida}={ema_fast:.2f} / "
            f"EMA{cfg.ema_lenta}={ema_slow:.2f} | RSI={rsi:.1f} | {modo_info}"
        )

        signal = AccionesSignal(
            simbolo=simbolo,
            precio_limite=precio,
            cantidad=cantidad,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rsi=rsi,
            razon=razon,
            score=abs(ema_fast - ema_slow) / precio * 100,
        )
        self._last_signal = signal

        logger.info(
            "Acciones EMA: Comprando %s x%d @ %.2f | %s",
            simbolo, cantidad, precio, razon,
        )

        order = await self._oms.open_position(
            simbolo=simbolo,
            tipo="ACCION",
            lado="LONG",
            cantidad=cantidad,
            precio_limite=precio,
        )

        if order.estado.value in ("rechazada", "cancelada"):
            logger.warning(
                "Acciones EMA: Orden rechazada para %s: %s",
                simbolo, order.estado.value,
            )

    # ── Monitoreo SL/TP ──────────────────────────────────────────────────

    async def _monitorear_posiciones(self, precio_actual: float) -> None:
        """Revisa SL/TP de cada posición abierta."""
        cfg = self._config

        for pos in self._oms.posiciones_abiertas():
            if pos.lado != "LONG":
                continue

            precio_pagado = pos.precio_apertura
            if precio_pagado <= 0:
                continue

            variacion = (precio_actual - precio_pagado) / precio_pagado
            razon: str | None = None

            if variacion <= -cfg.stop_loss_pct:
                razon = (
                    f"STOP-LOSS ({variacion * 100:+.1f}% <= -{cfg.stop_loss_pct * 100:.0f}%)"
                )
            elif variacion >= cfg.take_profit_pct:
                razon = (
                    f"TAKE-PROFIT ({variacion * 100:+.1f}% >= +{cfg.take_profit_pct * 100:.0f}%)"
                )
            else:
                logger.info(
                    "📈 SL/TP %s: var=%+.1f%% (SL=%.0f%% TP=+%.0f%%) actual=%.2f comprado=%.2f",
                    pos.simbolo, variacion * 100,
                    -cfg.stop_loss_pct * 100, cfg.take_profit_pct * 100,
                    precio_actual, precio_pagado,
                )

            if razon:
                logger.info(
                    "Acciones EMA: Cerrando %s por %s", pos.simbolo, razon,
                )
                await self._oms.close_position(pos.id, precio_limite=precio_actual)
                self._cooldown_counter = cfg.cooldown_snapshots

    # ── Cierre masivo (EOD / Death Cross) ────────────────────────────────

    async def cerrar_todo(self, precio_actual: float, razon: str = "cierre forzado") -> None:
        """Cierra todas las posiciones abiertas."""
        abiertas = self._oms.posiciones_abiertas()
        if not abiertas:
            return
        logger.warning(
            "Acciones EMA: Cierre masivo (%s) — %d posiciones",
            razon, len(abiertas),
        )
        for pos in abiertas:
            await self._oms.close_position(pos.id, precio_limite=precio_actual)

    async def cerrar_todos(self, snapshot: StockSnapshot) -> None:
        """Alias para compatibilidad con el engine (cierre EOD)."""
        precio = snapshot.precio or 0.0
        await self.cerrar_todo(precio, razon="Cierre EOD 16:45")
