"""
Módulo: Estrategia RSI Options (Solo RSI, sin EMA)

Estrategia intradiaria basada únicamente en RSI del subyacente:
  - RSI > rsi_overbought  → compra PUT  ATM (mercado sobrecomprado, esperamos corrección)
  - RSI < rsi_oversold    → compra CALL ATM (mercado sobrevendido, esperamos rebote)

NUNCA lanza/vende opciones en descubierto. Solo compra para abrir
y vende para cerrar, tradeando el valor de la prima.

Gestión: Stop-loss y Take-profit sobre el precio de la prima pagada.
Cierre forzado a 16:45 para bonificación intradiaria IOL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from candles import CandleAggregator
from market_data import MarketSnapshot
from math_engine import OptionPricing, enrich_snapshot, DEFAULT_RISK_FREE_RATE

if TYPE_CHECKING:
    from oms import OMS

logger = logging.getLogger(__name__)


# ─── Configuración ────────────────────────────────────────────────────────────

@dataclass
class RsiOptionsConfig:
    """Parámetros de la estrategia RSI Options."""

    # ── RSI ──────────────────────────────────────────────────────────────
    rsi_periodos: int = 14
    """Períodos para el cálculo del RSI."""

    rsi_overbought: float = 70.0
    """RSI por encima de este umbral → comprar PUT (sobrecompra)."""

    rsi_oversold: float = 30.0
    """RSI por debajo de este umbral → comprar CALL (sobreventa)."""

    # ── Timeframe de velas ───────────────────────────────────────────────
    candle_minutes: int = 5
    """Tamaño de vela OHLC en minutos. Se construyen agregando ticks del spot."""

    # ── Selección de opciones ────────────────────────────────────────────
    target_delta: float = 0.50
    """Delta absoluto objetivo (0.50 = ATM). Busca la opción más cercana."""

    min_dte: int = 5
    """DTE mínimo."""

    max_dte: int = 45
    """DTE máximo."""

    max_spread_pct: float = 0.40
    """Spread bid-ask máximo tolerable en la opción."""

    # ── Gestión de riesgo ────────────────────────────────────────────────
    stop_loss_pct: float = 0.20
    """Vender si la prima cae este % de lo pagado (ej: 0.20 = -20%)."""

    take_profit_pct: float = 0.40
    """Vender si la prima sube este % sobre lo pagado (ej: 0.40 = +40%)."""

    # ── Tamaño y límites ─────────────────────────────────────────────────
    lotes_por_trade: int = 1
    """Contratos por orden."""

    max_posiciones: int = 2
    """Máximo de posiciones abiertas simultáneas."""

    cooldown_snapshots: int = 5
    """Snapshots de espera post-entrada para evitar re-entrar mientras RSI sigue en extremo."""

    # ── Control intradiario ──────────────────────────────────────────────
    force_intraday_close: bool = True
    """Cerrar todo a las 16:45 para bonificación IOL."""


# ─── Señal de trading ─────────────────────────────────────────────────────────

@dataclass
class RsiOptionsSignal:
    """Señal generada por el RSI."""
    direccion:     str           # "CALL" | "PUT"
    opcion:        OptionPricing
    precio_limite: float
    cantidad:      int
    razon:         str
    score:         float         # distancia del RSI al umbral (mayor = más extremo)

    @property
    def informacion(self) -> str:
        q = self.opcion.quote
        return (
            f"COMPRA {self.direccion} {q.simbolo} "
            f"(K={q.strike:.1f} DTE={q.dias_al_vencimiento}) "
            f"@ {self.precio_limite:.2f} | {self.razon}"
        )


# ─── Estrategia ───────────────────────────────────────────────────────────────

class RsiOptionsStrategy:
    """
    Motor de trading intradiario sobre primas de opciones usando RSI puro.

    Compra PUTs cuando el RSI supera el umbral de sobrecompra.
    Compra CALLs cuando el RSI cae bajo el umbral de sobreventa.
    Nunca lanza (vende) opciones — solo tradea el precio de la prima.
    """

    def __init__(self, oms: "OMS", config: RsiOptionsConfig | None = None) -> None:
        self._oms = oms
        self._config = config or RsiOptionsConfig()

        self._aggregator = CandleAggregator(timeframe_min=self._config.candle_minutes)
        self._cooldown_counter: int = 0

        self._last_signal: RsiOptionsSignal | None = None
        self._last_indicators: dict = {}
        self._last_order_attempt: dict | None = None  # {"direccion", "resultado", "razon"}

    # ── RSI ───────────────────────────────────────────────────────────────

    @staticmethod
    def _calc_rsi(prices: list[float], period: int) -> float:
        if len(prices) < period + 1:
            return 50.0
        changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        recent = changes[-period:]
        gains = [c for c in recent if c > 0]
        losses = [-c for c in recent if c < 0]
        avg_gain = sum(gains) / period if gains else 0.0
        avg_loss = sum(losses) / period if losses else 0.0
        if avg_loss == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    # ── Selección de opción ───────────────────────────────────────────────

    def _seleccionar_opcion(
        self,
        pricings: list[OptionPricing],
        tipo: str,
    ) -> OptionPricing | None:
        cfg = self._config
        candidatos: list[tuple[float, OptionPricing]] = []

        for p in pricings:
            q = p.quote
            if q.tipo != tipo:
                continue
            if q.bid is None or q.ask is None or q.ask <= 0:
                continue
            spread = q.spread_pct
            if spread is None or spread > cfg.max_spread_pct:
                continue
            dte = q.dias_al_vencimiento
            if dte < cfg.min_dte or dte > cfg.max_dte:
                continue
            if p.greeks.delta is None or p.greeks.iv is None:
                continue
            delta_dist = abs(abs(p.greeks.delta) - cfg.target_delta)
            candidatos.append((delta_dist, p))

        if not candidatos:
            logger.info("RSI: selección %s — 0 candidatos tras filtros (DTE/liquidez/delta)", tipo)
            return None

        candidatos.sort(key=lambda x: x[0])
        best = candidatos[0][1]
        logger.info(
            "RSI: selección %s — %d candidatos. Mejor=%s (K=%.1f Δ=%.2f ask=%.2f DTE=%d)",
            tipo, len(candidatos), best.quote.simbolo,
            best.quote.strike, best.greeks.delta, best.quote.ask or 0,
            best.quote.dias_al_vencimiento,
        )
        return best

    # ── API pública ───────────────────────────────────────────────────────

    async def on_snapshot(self, snapshot: MarketSnapshot) -> None:
        spot = snapshot.spot
        if spot is None or spot <= 0:
            return

        # Defense in depth: no analizar fuera de horario de mercado
        from datetime import datetime, time as _t, timezone, timedelta
        _now_arg = datetime.now(tz=timezone(timedelta(hours=-3))).time()
        if not (_t(10, 30) <= _now_arg < _t(17, 0)):
            return

        cfg = self._config

        # Agregar tick al constructor de velas
        closed_candle = self._aggregator.add_tick(spot)
        closes = self._aggregator.closes

        # Calcular RSI sobre cierres de velas cerradas
        if len(closes) >= cfg.rsi_periodos + 1:
            rsi = self._calc_rsi(closes, cfg.rsi_periodos)
        else:
            rsi = None

        self._last_indicators = {
            "rsi": round(rsi, 1) if rsi is not None else None,
            "spot": spot,
            "candles_closed": len(closes),
            "tf_min": cfg.candle_minutes,
            "warmup": rsi is None,
        }

        pricings = enrich_snapshot(snapshot, r=DEFAULT_RISK_FREE_RATE)

        # Monitoreo SL/TP corre en cada tick (no esperamos cierre de vela para salir)
        await self._monitorear_posiciones(snapshot, pricings)

        # Las decisiones de entrada solo se evalúan al CIERRE de una vela
        if closed_candle is None:
            return  # estamos a mitad de vela
        if rsi is None:
            return  # warmup pendiente

        if self._cooldown_counter > 0:
            self._cooldown_counter -= 1
            return

        abiertas = self._oms.posiciones_abiertas()
        if len(abiertas) >= cfg.max_posiciones:
            return

        if rsi > cfg.rsi_overbought:
            logger.info("RSI=%.1f > %.0f (sobrecompra, vela %dm cerrada) → señal PUT", rsi, cfg.rsi_overbought, cfg.candle_minutes)
            await self._intentar_entrada("PUT", pricings, spot, rsi)
        elif rsi < cfg.rsi_oversold:
            logger.info("RSI=%.1f < %.0f (sobreventa, vela %dm cerrada) → señal CALL", rsi, cfg.rsi_oversold, cfg.candle_minutes)
            await self._intentar_entrada("CALL", pricings, spot, rsi)

    # ── Entrada ───────────────────────────────────────────────────────────

    async def _intentar_entrada(
        self,
        tipo: str,
        pricings: list[OptionPricing],
        spot: float,
        rsi: float,
    ) -> None:
        cfg = self._config

        for p in self._oms.posiciones_abiertas():
            if p.tipo == tipo:
                self._last_order_attempt = {"direccion": tipo, "resultado": "bloqueado", "razon": f"Ya hay posición {tipo} abierta"}
                return

        opcion = self._seleccionar_opcion(pricings, tipo)
        if opcion is None:
            # ── Diagnóstico granular por opción ────────────────────────────
            buckets = {"sin_bid": 0, "sin_ask": 0, "fuera_dte": 0, "spread_alto": 0, "sin_iv": 0, "candidato": 0}
            muestras: list[tuple[float, dict]] = []

            for p in pricings:
                if p.quote.tipo != tipo:
                    continue
                q = p.quote

                estado = {
                    "simbolo": q.simbolo,
                    "K": q.strike,
                    "dte": q.dias_al_vencimiento,
                    "bid": q.bid,
                    "ask": q.ask,
                    "ult": q.ultimo,
                    "puntas_raw": q.puntas_raw,
                }

                # Clasificar por orden de filtros
                if q.bid is None:
                    bucket = "sin_bid"
                elif q.ask is None or q.ask <= 0:
                    bucket = "sin_ask"
                elif not (cfg.min_dte <= q.dias_al_vencimiento <= cfg.max_dte):
                    bucket = "fuera_dte"
                elif q.spread_pct is not None and q.spread_pct > cfg.max_spread_pct:
                    bucket = "spread_alto"
                    estado["spread"] = f"{q.spread_pct * 100:.0f}%"
                elif p.greeks.delta is None or p.greeks.iv is None:
                    bucket = "sin_iv"
                else:
                    bucket = "candidato"
                    estado["delta"] = round(p.greeks.delta, 2)
                    estado["iv"] = f"{p.greeks.iv * 100:.0f}%"

                estado["bucket"] = bucket
                buckets[bucket] += 1
                muestras.append((abs(q.strike - spot), estado))

            # Top 3 PUTs/CALLs más cercanas a ATM
            muestras.sort(key=lambda x: x[0])
            top3 = [e for _, e in muestras[:3]]

            total = sum(buckets.values())
            desglose_str = (
                f"sin_bid={buckets['sin_bid']} sin_ask={buckets['sin_ask']} "
                f"fuera_dte={buckets['fuera_dte']} spread_alto={buckets['spread_alto']} "
                f"sin_iv={buckets['sin_iv']} candidato={buckets['candidato']}"
            )

            # Construir las líneas para el UI log
            desglose_lines: list[str] = [
                f"⚠️ Sin {tipo} viable. {total} totales — {desglose_str}",
                f"Top 3 cerca de ATM (spot={spot:.0f}, DTE {cfg.min_dte}–{cfg.max_dte}, max_spread={cfg.max_spread_pct * 100:.0f}%):",
            ]
            for e in top3:
                bid_s = "null" if e["bid"] is None else f"{e['bid']:.2f}"
                ask_s = "null" if e["ask"] is None else f"{e['ask']:.2f}"
                ult_s = "null" if e["ult"] is None else f"{e['ult']:.2f}"
                extras = []
                if "spread" in e: extras.append(f"spread={e['spread']}")
                if "delta" in e: extras.append(f"Δ={e['delta']}")
                if "iv" in e: extras.append(f"IV={e['iv']}")
                extras_s = (" " + " ".join(extras)) if extras else ""
                desglose_lines.append(
                    f"  {e['simbolo']} K={e['K']:.0f} dte={e['dte']} bid={bid_s} ask={ask_s} ult={ult_s}{extras_s} → {e['bucket']}"
                )
                # Para sin_bid/sin_ask, agregar el dato crudo de IOL para depurar
                if e["bucket"] in ("sin_bid", "sin_ask"):
                    desglose_lines.append(f"    puntas_raw={e['puntas_raw']!r}")

            razon_principal = f"{tipo}: {desglose_str} (DTE {cfg.min_dte}–{cfg.max_dte})"
            logger.warning("RSI: no se encontró %s viable. %s", tipo, razon_principal)
            self._last_order_attempt = {
                "direccion": tipo,
                "resultado": "sin_opcion",
                "razon": razon_principal,
                "desglose": desglose_lines,
            }
            return

        q = opcion.quote
        precio = q.ask

        umbral = cfg.rsi_overbought if tipo == "PUT" else cfg.rsi_oversold
        distancia = abs(rsi - umbral)

        signal = RsiOptionsSignal(
            direccion=tipo,
            opcion=opcion,
            precio_limite=precio,
            cantidad=cfg.lotes_por_trade,
            razon=(
                f"RSI={rsi:.1f} ({'sobrecompra' if tipo == 'PUT' else 'sobreventa'}) "
                f"Δ={opcion.greeks.delta:.2f} IV={opcion.greeks.iv:.1%}"
            ),
            score=distancia,
        )
        self._last_signal = signal

        logger.info("RSI Options: Abriendo → %s", signal.informacion)

        order = await self._oms.open_position(
            simbolo=q.simbolo,
            tipo=tipo,
            lado="LONG",
            cantidad=cfg.lotes_por_trade,
            precio_limite=precio,
        )

        if order.estado.value in ("rechazada", "cancelada"):
            razon_rechazo = f"Orden rechazada por OMS/IOL: {q.simbolo} → {order.estado.value}"
            logger.warning("RSI Options: %s", razon_rechazo)
            self._last_order_attempt = {"direccion": tipo, "resultado": "rechazada", "razon": razon_rechazo}
        else:
            self._last_order_attempt = {"direccion": tipo, "resultado": "ok", "razon": signal.informacion}
            self._cooldown_counter = cfg.cooldown_snapshots

    # ── Monitoreo de posiciones (SL/TP) ───────────────────────────────────

    async def _monitorear_posiciones(
        self,
        snapshot: MarketSnapshot,
        pricings: list[OptionPricing],
    ) -> None:
        cfg = self._config
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
                razon = f"STOP-LOSS (prima {variacion * 100:+.1f}% <= -{cfg.stop_loss_pct * 100:.0f}%)"
            elif variacion >= cfg.take_profit_pct:
                razon = f"TAKE-PROFIT (prima {variacion * 100:+.1f}% >= +{cfg.take_profit_pct * 100:.0f}%)"
            else:
                logger.info(
                    "RSI monitor %s: var=%.1f%% (SL=%.0f%% TP=+%.0f%%) mid=%.2f pagado=%.2f",
                    pos.simbolo, variacion * 100,
                    -cfg.stop_loss_pct * 100, cfg.take_profit_pct * 100,
                    valor_actual, precio_pagado,
                )

            if razon:
                logger.info("RSI Options: Cerrando %s por %s", pos.simbolo, razon)
                await self._oms.close_position(pos.id, precio_limite=valor_actual)
                self._cooldown_counter = cfg.cooldown_snapshots

    # ── Cierre intradiario ────────────────────────────────────────────────

    async def cerrar_todos(self, snapshot: MarketSnapshot) -> None:
        abiertas = self._oms.posiciones_abiertas()
        if not abiertas:
            return

        mid_map: dict[str, float] = {}
        for opt in snapshot.opciones:
            if opt.mid:
                mid_map[opt.simbolo] = opt.mid

        logger.warning("RSI Options: Cierre intradiario forzado — cerrando %d posiciones", len(abiertas))
        for pos in abiertas:
            precio = mid_map.get(pos.simbolo) or pos.precio_apertura
            await self._oms.close_position(pos.id, precio_limite=precio)
