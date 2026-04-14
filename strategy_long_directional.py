"""
Módulo: Estrategia Long Directional Pura

Estrategia algorítmica enfocada estrictamente en la COMPRA SECA de opciones (Long Call o Long Put),
evitando por completo el uso de márgenes de garantía, descubiertos o construcciones de spreads.
Diseñada para capitalizar expectativas direccionales con riesgo limitado y pre-definido (la prima).
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
class LongDirectionalConfig:
    """Parámetros de la estrategia Long Directional."""

    # ── Dirección ─────────────────────────────────────────────────────────
    sesgo: str = "CALL"
    """
    Orientación dictada manualmente por el usuario.
    'CALL' para escenarios alcistas.
    'PUT' para escenarios bajistas.
    """

    # ── Selección de Opciones ─────────────────────────────────────────────
    target_delta_abs: float = 0.50
    """
    Delta absoluto objetivo para buscar opciones ATM.
    Generalmente un delta de ~0.50 corresponde a una opción At The Money.
    El algoritmo filtrará opciones cuyo valor esté cerca a este target (±0.15).
    """

    max_premium_pct: float = 0.10
    """
    Prima máxima permisible como porcentaje del valor del subyacente.
    Filtra opciones inusualmente caras debido a Volatilidad Implícita extrema.
    """

    # ── Filtros temporales ────────────────────────────────────────────────
    min_dte: int = 1
    """DTE mínimo (1 = dejar pasar todo para testing)."""

    max_dte: int = 90
    """DTE máximo."""

    # ── Filtros de liquidez ───────────────────────────────────────────────
    max_spread_pct: float = 0.25
    """Spread bid-ask máximo tolerado."""

    # ── Gestión de tamaño y límite ────────────────────────────────────────
    lotes_por_trade: int = 1
    """Contratos comprados concurrentes por señal."""

    max_posiciones_abiertas: int = 2
    """Máximo de posiciones simultáneas activas exclusivas por este bot."""

    # ── Take Profit y Stop Loss ───────────────────────────────────────────
    stop_loss_pct: float = 0.35
    """Corta pérdida si la prima cae X% de lo pagado (ej 0.35 = -35% pérdida)."""

    take_profit_pct: float = 0.60
    """Toma la liquidez si el valor de prima se incrementa X% sobre el costo."""

    force_intraday_close: bool = False
    """Evita dormir con las posiciones abiertas, cerrando todo cerca de fin de rueda."""


# ─── Señal Direccional ────────────────────────────────────────────────────────

@dataclass
class LongSignal:
    """Señal de entrada para una posición Long Seca."""
    opcion: OptionPricing
    costo: float
    score: float

    @property
    def informacion(self) -> str:
        return (
            f"LONG {self.opcion.quote.simbolo} (K={self.opcion.quote.strike:.2f}) | "
            f"Delta={self.opcion.greeks.delta:.2f} | Costo={self.costo:.2f} | "
            f"DTE={self.opcion.quote.dias_al_vencimiento} | Score={self.score:.2f}"
        )


# ─── Estrategia ───────────────────────────────────────────────────────────────

class LongDirectionalStrategy:
    def __init__(self, oms: "OMS", config: LongDirectionalConfig | None = None) -> None:
        self._oms = oms
        self._config = config or LongDirectionalConfig()
        self._last_evaluated_signals: list[LongSignal] = []

    async def on_snapshot(self, snapshot: MarketSnapshot) -> None:
        if snapshot.spot is None or snapshot.spot <= 0:
            return

        pricings = enrich_snapshot(snapshot, r=DEFAULT_RISK_FREE_RATE)
        if not pricings:
            return

        # 1. Monitorear abiertas (Trailing/StopLoss/TakeProfit)
        await self._monitorear_posiciones(snapshot, pricings)

        # 2. Reveer límite de posiciones activas
        from oms import PositionStatus
        activas = [p for p in self._oms.posiciones_abiertas()]
        
        # Filtramos las cerradas por parte del monitor si las hubiera en memoria temporal.
        if len(activas) >= self._config.max_posiciones_abiertas:
            return

        # 3. Evaluar señales de ingreso nuevas
        signals = self.evaluar(pricings, snapshot.spot)
        self._last_evaluated_signals = signals
        if not signals:
            return

        await self._ejecutar_signals(signals, activas)

    def evaluar(self, pricings: list[OptionPricing], spot: float) -> list[LongSignal]:
        cfg = self._config
        candidatos = [p for p in pricings if p.quote.tipo == cfg.sesgo.upper()]
        
        signals = []
        stats = {"total": len(candidatos), "sin_puntas": 0, "spread": 0, "dte": 0,
                 "sin_delta": 0, "delta_rango": 0, "max_premium": 0, "ok": 0}

        for p in candidatos:
            q = p.quote
            
            # Liquidez y Spread
            if q.ask is None or q.bid is None or q.bid <= 0:
                stats["sin_puntas"] += 1
                continue
            spread_pct = q.spread_pct
            if spread_pct is None or spread_pct > cfg.max_spread_pct:
                stats["spread"] += 1
                continue

            # Temporalidad
            dte = q.dias_al_vencimiento
            if dte < cfg.min_dte or dte > cfg.max_dte:
                stats["dte"] += 1
                continue

            # Filtro Delta Target (La volatilidad o probabilidad real)
            delta = p.greeks.delta
            if delta is None:
                stats["sin_delta"] += 1
                continue

            delta_abs = abs(delta)
            
            # Tolerancia al objetivo del Delta: ±0.15 para abarcar una campana sana
            if abs(delta_abs - cfg.target_delta_abs) > 0.15:
                stats["delta_rango"] += 1
                continue

            # Límite Máximo de Prima (no pagar sobre-precios inflados por IV)
            costo = q.ask
            if costo > (spot * cfg.max_premium_pct):
                stats["max_premium"] += 1
                continue
            
            # El Score beneficia a las de Delta más alineado, IV más bajo (relativo),
            # y menor ratio de fricción bid/ask
            score = (1.0 / (abs(delta_abs - cfg.target_delta_abs) + 0.01)) * (1.0 - spread_pct)

            signals.append(LongSignal(
                opcion=p,
                costo=costo,
                score=score
            ))
            stats["ok"] += 1

        # Las mejores configuraciones primero
        signals.sort(key=lambda s: s.score, reverse=True)

        # Logging ultra-verboso
        logger.info(
            "🔍 Long Dir [%s]: %d candidatos | Señales=%d | "
            "Filtradas → sin_puntas=%d, spread=%d, dte=%d, sin_delta=%d, delta_rango=%d, max_premium=%d",
            cfg.sesgo, stats["total"], stats["ok"],
            stats["sin_puntas"], stats["spread"], stats["dte"],
            stats["sin_delta"], stats["delta_rango"], stats["max_premium"],
        )
        for s in signals[:5]:
            q = s.opcion.quote
            logger.info(
                "  → %s K=%.2f Δ=%.2f ask=%.2f DTE=%d score=%.2f",
                q.simbolo, q.strike, s.opcion.greeks.delta, q.ask, q.dias_al_vencimiento, s.score,
            )

        return signals

    async def _ejecutar_signals(self, signals: list[LongSignal], activas: list) -> None:
        cfg = self._config
        n_open = len(activas)
        
        # Filtrar simbolos que ya tenemos en cartera para no duplicar entradas promediadas
        simbolos_abiertos = {p.simbolo for p in activas}

        for signal in signals:
            if n_open >= cfg.max_posiciones_abiertas:
                break
            
            simbolo = signal.opcion.quote.simbolo
            if simbolo in simbolos_abiertos:
                continue
                
            logger.info("LongDirectional: Abriendo %s", signal.informacion)
            
            order = await self._oms.open_position(
                simbolo=simbolo,
                tipo=signal.opcion.quote.tipo,
                lado="LONG",
                cantidad=cfg.lotes_por_trade,
                precio_limite=signal.opcion.quote.ask
            )
            
            if order.estado.value not in ("rechazada", "cancelada"):
                n_open += 1
                simbolos_abiertos.add(simbolo)

    async def _monitorear_posiciones(self, snapshot: MarketSnapshot, pricings: list[OptionPricing]) -> None:
        cfg = self._config
        
        mid_map = {p.quote.simbolo: p.quote.mid for p in pricings if p.quote.mid}
        
        for p in self._oms.posiciones_abiertas():
            if p.lado != "LONG":
                continue
                
            valor_actual = mid_map.get(p.simbolo)
            if not valor_actual:
                continue
                
            precio_pagado = p.precio_apertura
            
            # PnL % = (Actual - Inicial) / Inicial
            # Evaluado relativo a la compra, sin considerar loteos, unitariamente
            variacion = (valor_actual - precio_pagado) / precio_pagado
            
            razon: str | None = None
            
            if variacion <= -cfg.stop_loss_pct:
                razon = f"STOP-LOSS ({variacion*100:.1f}% <= {-cfg.stop_loss_pct*100:.1f}%)"
            elif variacion >= cfg.take_profit_pct:
                razon = f"TAKE-PROFIT ({variacion*100:.1f}% >= {cfg.take_profit_pct*100:.1f}%)"
            else:
                logger.info(
                    "📈 LD monitor %s: var=%.1f%% (SL=%.0f%% TP=+%.0f%%) mid=%.2f pagado=%.2f",
                    p.simbolo, variacion * 100,
                    -cfg.stop_loss_pct * 100, cfg.take_profit_pct * 100,
                    valor_actual, precio_pagado,
                )
                
            if razon:
                logger.info("LongDirectional: Cerrando %s por %s", p.simbolo, razon)
                await self._oms.close_position(p.id, precio_limite=valor_actual)
            
    async def cerrar_todos(self, snapshot: MarketSnapshot) -> None:
        for p in self._oms.posiciones_abiertas():
            # Buscar un mid o bid/ask viable
            v = [o.mid for o in snapshot.opciones if o.simbolo == p.simbolo and o.mid]
            precio = v[0] if v else 0.01
            await self._oms.close_position(p.id, precio_limite=precio)
