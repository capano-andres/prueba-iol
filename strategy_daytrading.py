"""
Módulo: Estrategia Daytrading Opciones GGAL (Bidireccional)

Estrategia intradiaria que tradea el VALOR DE LA PRIMA de opciones:
  - Tendencia alcista (EMA golden cross)  → COMPRA CALL ATM
  - Tendencia bajista (EMA death cross)   → COMPRA PUT ATM

NUNCA lanza/vende opciones en descubierto. Solo compra para abrir
y vende para cerrar, tradeando el precio de la prima.

Indicadores: EMA rápida/lenta crossover + RSI como filtro.
Gestión: Stop-loss y Take-profit sobre el precio de la prima pagada.
Cierre forzado a 16:45 para bonificación intradiaria IOL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from market_data import MarketSnapshot, OptionQuote
from math_engine import OptionPricing, enrich_snapshot, DEFAULT_RISK_FREE_RATE

if TYPE_CHECKING:
    from oms import OMS

logger = logging.getLogger(__name__)


# ─── Configuración ────────────────────────────────────────────────────────────

@dataclass
class DaytradingConfig:
    """Parámetros de la estrategia Daytrading Opciones."""

    # ── Indicadores técnicos ─────────────────────────────────────────────
    ema_rapida: int = 9
    """Períodos para la EMA rápida. Cada período = 1 snapshot (~30s)."""

    ema_lenta: int = 21
    """Períodos para la EMA lenta. 21 snapshots ≈ 10.5 min de warmup."""

    rsi_periodos: int = 14
    """Períodos para el cálculo de RSI."""

    rsi_overbought: int = 70
    """No comprar CALLs si RSI supera este umbral (sobrecompra)."""

    rsi_oversold: int = 30
    """No comprar PUTs si RSI cae debajo de este umbral (sobreventa)."""

    # ── Selección de opciones ────────────────────────────────────────────
    target_delta: float = 0.50
    """Delta absoluto objetivo (0.50 = ATM). Busca la opción más cercana."""

    min_dte: int = 5
    """DTE mínimo para evitar Gamma extremo en la última semana."""

    max_dte: int = 45
    """DTE máximo."""

    max_spread_pct: float = 0.25
    """Spread bid-ask máximo tolerable en la opción."""

    # ── Gestión de riesgo ────────────────────────────────────────────────
    stop_loss_pct: float = 0.20
    """Vender la opción si la prima cae X% de lo pagado (ej: 0.20 = -20%)."""

    take_profit_pct: float = 0.40
    """Vender la opción si la prima sube X% sobre lo pagado (ej: 0.40 = +40%)."""

    # ── Tamaño y límites ─────────────────────────────────────────────────
    lotes_por_trade: int = 1
    """Contratos por orden."""

    max_posiciones: int = 2
    """Máximo de posiciones abiertas simultáneas."""

    cooldown_snapshots: int = 5
    """Snapshots de espera después de cerrar antes de abrir otra (~2.5 min)."""

    # ── Control intradiario ──────────────────────────────────────────────
    force_intraday_close: bool = True
    """Cerrar todo a las 16:45 para bonificación IOL."""


# ─── Señal de trading ─────────────────────────────────────────────────────────

@dataclass
class DaytradingSignal:
    """Señal generada por los indicadores técnicos."""
    direccion:     str              # "CALL" | "PUT"
    opcion:        OptionPricing    # Opción seleccionada
    precio_limite: float            # Ask (precio de compra)
    cantidad:      int
    razon:         str
    score:         float

    @property
    def informacion(self) -> str:
        q = self.opcion.quote
        return (
            f"COMPRA {self.direccion} {q.simbolo} "
            f"(K={q.strike:.1f} DTE={q.dias_al_vencimiento}) "
            f"@ {self.precio_limite:.2f} | {self.razon}"
        )


# ─── Estrategia ───────────────────────────────────────────────────────────────

class DaytradingStrategy:
    """
    Motor de daytrading bidireccional sobre primas de opciones GGAL.

    Compra CALLs cuando la tendencia es alcista (EMA golden cross).
    Compra PUTs cuando la tendencia es bajista (EMA death cross).
    Nunca lanza (vende) opciones — solo tradea el precio de la prima.
    """

    def __init__(self, oms: "OMS", config: DaytradingConfig | None = None) -> None:
        self._oms = oms
        self._config = config or DaytradingConfig()

        # Historial de precios spot para indicadores
        self._price_history: list[float] = []

        # Estado del cruce EMA
        self._prev_ema_fast: float | None = None
        self._prev_ema_slow: float | None = None

        # Cooldown post-cierre
        self._cooldown_counter: int = 0

        # Última señal evaluada (para serialización UI)
        self._last_signal: DaytradingSignal | None = None
        self._last_indicators: dict = {}

    # ── Indicadores técnicos ──────────────────────────────────────────────

    @staticmethod
    def _calc_ema(prices: list[float], period: int) -> float:
        """
        Calcula EMA (Exponential Moving Average).
        Usa todo el historial disponible con suavización 2/(period+1).
        """
        if len(prices) < period:
            # No hay suficientes datos; retornar SMA como fallback
            return sum(prices) / len(prices)

        multiplier = 2.0 / (period + 1)
        # Seed: SMA de los primeros `period` valores
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    @staticmethod
    def _calc_rsi(prices: list[float], period: int) -> float:
        """
        Calcula RSI (Relative Strength Index).
        Retorna valor entre 0 y 100.
        """
        if len(prices) < period + 1:
            return 50.0  # neutral si no hay datos

        # Calcular cambios
        changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

        # Usar los últimos `period` cambios
        recent = changes[-period:]
        gains = [c for c in recent if c > 0]
        losses = [-c for c in recent if c < 0]

        avg_gain = sum(gains) / period if gains else 0.0
        avg_loss = sum(losses) / period if losses else 0.0

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    # ── Selección de opción ───────────────────────────────────────────────

    def _seleccionar_opcion(
        self,
        pricings: list[OptionPricing],
        tipo: str,  # "CALL" | "PUT"
    ) -> OptionPricing | None:
        """
        Selecciona la mejor opción (CALL o PUT) para tradear:
        - Filtra por tipo, DTE, liquidez, spread
        - Elige la de |delta| más cercano al target (ATM)
        """
        cfg = self._config
        candidatos: list[tuple[float, OptionPricing]] = []

        for p in pricings:
            q = p.quote

            # Filtro de tipo
            if q.tipo != tipo:
                continue

            # Filtro de liquidez
            if q.bid is None or q.ask is None or q.ask <= 0:
                continue

            # Filtro de spread
            spread = q.spread_pct
            if spread is None or spread > cfg.max_spread_pct:
                continue

            # Filtro de DTE
            dte = q.dias_al_vencimiento
            if dte < cfg.min_dte or dte > cfg.max_dte:
                continue

            # Filtro de delta calculable
            if p.greeks.delta is None or p.greeks.iv is None:
                continue

            # Distancia al delta objetivo
            delta_dist = abs(abs(p.greeks.delta) - cfg.target_delta)
            candidatos.append((delta_dist, p))

        if not candidatos:
            return None

        # Ordenar por cercanía al delta target
        candidatos.sort(key=lambda x: x[0])
        return candidatos[0][1]

    # ── API pública ───────────────────────────────────────────────────────

    async def on_snapshot(self, snapshot: MarketSnapshot) -> None:
        """
        Callback del MarketDataFeed. En cada tick:
        1. Acumula spot en historial
        2. Calcula indicadores (EMA, RSI)
        3. Monitorea posiciones abiertas (SL/TP)
        4. Detecta cruces EMA → genera y ejecuta señal
        """
        spot = snapshot.spot
        if spot is None or spot <= 0:
            return

        # 1. Acumular precio
        self._price_history.append(spot)

        # Limitar historial a 500 puntos (evitar memory leak)
        if len(self._price_history) > 500:
            self._price_history = self._price_history[-500:]

        cfg = self._config

        # 2. Verificar warmup
        min_data = max(cfg.ema_lenta, cfg.rsi_periodos + 1)
        if len(self._price_history) < min_data:
            logger.debug(
                "Daytrading: Warmup %d/%d snapshots",
                len(self._price_history), min_data,
            )
            return

        # 3. Calcular indicadores
        ema_fast = self._calc_ema(self._price_history, cfg.ema_rapida)
        ema_slow = self._calc_ema(self._price_history, cfg.ema_lenta)
        rsi = self._calc_rsi(self._price_history, cfg.rsi_periodos)

        self._last_indicators = {
            "ema_fast": round(ema_fast, 2),
            "ema_slow": round(ema_slow, 2),
            "rsi": round(rsi, 1),
            "spot": spot,
            "data_points": len(self._price_history),
        }

        # 4. Enriquecer opciones con griegas (necesario para selección)
        pricings = enrich_snapshot(snapshot, r=DEFAULT_RISK_FREE_RATE)

        # 5. Monitorear posiciones abiertas (SL/TP)
        await self._monitorear_posiciones(snapshot, pricings)

        # 6. Cooldown post-cierre
        if self._cooldown_counter > 0:
            self._cooldown_counter -= 1
            return

        # 7. Detectar cruce EMA
        if self._prev_ema_fast is not None and self._prev_ema_slow is not None:
            prev_diff = self._prev_ema_fast - self._prev_ema_slow
            curr_diff = ema_fast - ema_slow

            # Golden Cross: EMA rápida cruza por encima de EMA lenta
            if prev_diff <= 0 and curr_diff > 0:
                logger.info(
                    "Daytrading: GOLDEN CROSS detectado (EMA%d=%.2f > EMA%d=%.2f, RSI=%.1f)",
                    cfg.ema_rapida, ema_fast, cfg.ema_lenta, ema_slow, rsi,
                )
                if rsi < cfg.rsi_overbought:
                    await self._intentar_entrada("CALL", pricings, spot, ema_fast, ema_slow, rsi)
                else:
                    logger.info("Daytrading: Golden Cross ignorado — RSI=%.1f > %d (sobrecompra)", rsi, cfg.rsi_overbought)

            # Death Cross: EMA rápida cruza por debajo de EMA lenta
            elif prev_diff >= 0 and curr_diff < 0:
                logger.info(
                    "Daytrading: DEATH CROSS detectado (EMA%d=%.2f < EMA%d=%.2f, RSI=%.1f)",
                    cfg.ema_rapida, ema_fast, cfg.ema_lenta, ema_slow, rsi,
                )
                if rsi > cfg.rsi_oversold:
                    await self._intentar_entrada("PUT", pricings, spot, ema_fast, ema_slow, rsi)
                else:
                    logger.info("Daytrading: Death Cross ignorado — RSI=%.1f < %d (sobreventa)", rsi, cfg.rsi_oversold)

        # Guardar EMAs para detectar cruce en el siguiente tick
        self._prev_ema_fast = ema_fast
        self._prev_ema_slow = ema_slow

    # ── Entrada ───────────────────────────────────────────────────────────

    async def _intentar_entrada(
        self,
        tipo: str,  # "CALL" | "PUT"
        pricings: list[OptionPricing],
        spot: float,
        ema_fast: float,
        ema_slow: float,
        rsi: float,
    ) -> None:
        """Intenta abrir una posición comprando la opción seleccionada."""
        cfg = self._config

        # Verificar límite de posiciones
        abiertas = self._oms.posiciones_abiertas()
        if len(abiertas) >= cfg.max_posiciones:
            logger.info(
                "Daytrading: Límite de posiciones alcanzado (%d/%d)",
                len(abiertas), cfg.max_posiciones,
            )
            return

        # No duplicar: no abrir si ya tenemos una posición del mismo tipo
        for p in abiertas:
            if p.tipo == tipo:
                logger.debug("Daytrading: Ya tenemos posición %s abierta, ignorando.", tipo)
                return

        # Seleccionar la mejor opción
        opcion = self._seleccionar_opcion(pricings, tipo)
        if opcion is None:
            logger.warning("Daytrading: No se encontró %s viable (DTE/liquidez/delta).", tipo)
            return

        q = opcion.quote
        precio = q.ask  # compramos al ask

        signal = DaytradingSignal(
            direccion=tipo,
            opcion=opcion,
            precio_limite=precio,
            cantidad=cfg.lotes_por_trade,
            razon=(
                f"EMA{cfg.ema_rapida}={ema_fast:.2f} vs EMA{cfg.ema_lenta}={ema_slow:.2f} "
                f"RSI={rsi:.1f} Δ={opcion.greeks.delta:.2f} IV={opcion.greeks.iv:.1%}"
            ),
            score=abs(ema_fast - ema_slow) / spot * 100,
        )
        self._last_signal = signal

        logger.info("Daytrading: Abriendo → %s", signal.informacion)

        order = await self._oms.open_position(
            simbolo=q.simbolo,
            tipo=tipo,
            lado="LONG",           # SIEMPRE compramos — nunca lanzamos
            cantidad=cfg.lotes_por_trade,
            precio_limite=precio,
        )

        if order.estado.value in ("rechazada", "cancelada"):
            logger.warning("Daytrading: Orden rechazada para %s: %s", q.simbolo, order.estado.value)

    # ── Monitoreo de posiciones (SL/TP) ───────────────────────────────────

    async def _monitorear_posiciones(
        self,
        snapshot: MarketSnapshot,
        pricings: list[OptionPricing],
    ) -> None:
        """Revisa SL/TP de cada posición abierta."""
        cfg = self._config

        # Mapa símbolo → mid actual
        mid_map = {p.quote.simbolo: p.quote.mid for p in pricings if p.quote.mid}

        for pos in self._oms.posiciones_abiertas():
            if pos.lado != "LONG":
                continue

            valor_actual = mid_map.get(pos.simbolo)
            if not valor_actual:
                continue

            precio_pagado = pos.precio_apertura
            variacion = (valor_actual - precio_pagado) / precio_pagado

            razon: str | None = None

            if variacion <= -cfg.stop_loss_pct:
                razon = (
                    f"STOP-LOSS (prima {variacion * 100:+.1f}% "
                    f"<= -{cfg.stop_loss_pct * 100:.0f}%)"
                )
            elif variacion >= cfg.take_profit_pct:
                razon = (
                    f"TAKE-PROFIT (prima {variacion * 100:+.1f}% "
                    f">= +{cfg.take_profit_pct * 100:.0f}%)"
                )

            if razon:
                logger.info("Daytrading: Cerrando %s por %s", pos.simbolo, razon)
                await self._oms.close_position(pos.id, precio_limite=valor_actual)
                self._cooldown_counter = cfg.cooldown_snapshots

    # ── Cierre intradiario ────────────────────────────────────────────────

    async def cerrar_todos(self, snapshot: MarketSnapshot) -> None:
        """Cierra todas las posiciones abiertas (pre-cierre 16:45)."""
        abiertas = self._oms.posiciones_abiertas()
        if not abiertas:
            return

        # Mapa mid
        mid_map: dict[str, float] = {}
        for opt in snapshot.opciones:
            if opt.mid:
                mid_map[opt.simbolo] = opt.mid

        logger.warning(
            "Daytrading: Cierre intradiario forzado — cerrando %d posiciones",
            len(abiertas),
        )
        for pos in abiertas:
            precio = mid_map.get(pos.simbolo) or pos.precio_apertura
            await self._oms.close_position(pos.id, precio_limite=precio)
