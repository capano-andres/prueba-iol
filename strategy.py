"""
Módulo 5: Motor de Estrategia
- Evalúa oportunidades de mispricing en la cadena de opciones GGAL
- Genera señales de compra/venta basadas en diferencia IV mercado vs BSM
- Ejecuta órdenes limitadas via OMS respetando límites de portafolio
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from market_data import MarketSnapshot
from math_engine import DEFAULT_RISK_FREE_RATE, OptionPricing, enrich_snapshot
from oms import OMS

logger = logging.getLogger(__name__)


# ─── Configuración ────────────────────────────────────────────────────────────

@dataclass
class StrategyConfig:
    """
    Parámetros de la estrategia. Los defaults son conservadores para empezar
    en dry-run y observar cuántas señales genera el mercado real.
    """

    # Señal de mispricing
    min_mispricing_pct: float = 0.05   # 5% del precio BSM para abrir posición

    # Filtros de liquidez
    min_spread_pct: float = 0.02       # spread mínimo 2%
    max_spread_pct: float = 0.30       # spread máximo 30% (evitar ilíquidas)

    # Filtros temporales
    min_dte: int = 1                   # mínimo 1 DTE (dejar pasar todo para testing)
    max_dte: int = 45                  # máximo 45 DTE

    # Filtros de delta
    min_delta_abs: float = 0.15        # evitar deep OTM (poco premium)
    max_delta_abs: float = 0.85        # evitar deep ITM (poco spread)

    # Tamaño de posición
    lote_base: int = 1                 # contratos por orden

    # Límites de portafolio
    max_posiciones_abiertas: int = 5


# ─── Señal de trading ─────────────────────────────────────────────────────────

@dataclass
class TradeSignal:
    """Señal de trading generada por la estrategia para una opción específica."""
    pricing:       OptionPricing
    lado:          str    # "SHORT" | "LONG"
    precio_limite: float
    cantidad:      int
    razon:         str    # descripción legible del motivo
    score:         float  # |mispricing_pct| — mayor = más atractivo


# ─── Estrategia ───────────────────────────────────────────────────────────────

class Strategy:
    """
    Motor de señales intradiario para opciones GGAL.

    Lógica central: si el precio de mercado difiere del teórico BSM en más de
    `min_mispricing_pct`, genera una señal:
      - SHORT: vender la prima cuando el mercado sobrevalora (mid > BSM)
      - LONG:  comprar la prima cuando el mercado subvalora  (mid < BSM)

    Uso típico:
        strategy = Strategy(oms)
        feed.on_snapshot(strategy.on_snapshot)
    """

    def __init__(self, oms: OMS, config: StrategyConfig | None = None) -> None:
        self._oms    = oms
        self._config = config or StrategyConfig()

    # ── API pública ───────────────────────────────────────────────────────

    def evaluar(self, pricings: list[OptionPricing]) -> tuple[list[TradeSignal], dict]:
        """
        Evalúa la lista de OptionPricing y retorna (señales, stats).
        - señales: ordenadas por score descendente.
        - stats: contadores de filtros para diagnóstico en UI.
        No ejecuta ninguna orden — solo analiza.
        """
        signals: list[TradeSignal] = []

        # Contadores de filtros para logging
        stats = {"total": 0, "sin_puntas": 0, "spread": 0, "dte": 0,
                 "sin_iv": 0, "delta": 0, "mispricing": 0, "near_miss": 0, "ok": 0}

        for p in pricings:
            stats["total"] += 1
            signal, reason = self._evaluar_uno(p)
            if signal is not None:
                signals.append(signal)
                stats["ok"] += 1
            elif reason:
                stats[reason] += 1

        signals.sort(key=lambda s: s.score, reverse=True)

        # Log ultra-verboso (terminal)
        logger.info(
            "📊 Mispricing eval: %d opciones | Señales=%d | "
            "Filtradas → sin_puntas=%d, spread=%d, dte=%d, sin_iv=%d, delta=%d, mispricing=%d (near_miss=%d)",
            stats["total"], stats["ok"],
            stats["sin_puntas"], stats["spread"], stats["dte"],
            stats["sin_iv"], stats["delta"], stats["mispricing"], stats["near_miss"],
        )
        for s in signals[:5]:
            logger.info(
                "  ✅ SEÑAL: %s %s score=%.3f precio=%.2f | %s",
                s.lado, s.pricing.quote.simbolo, s.score, s.precio_limite, s.razon,
            )

        return signals, stats

    async def ejecutar_signals(self, signals: list[TradeSignal]) -> None:
        """Ejecuta señales en orden de score respetando el límite de posiciones."""
        abiertas  = len(self._oms.posiciones_abiertas()) + len(self._oms.ordenes_pendientes())
        max_pos   = self._config.max_posiciones_abiertas

        for signal in signals:
            if abiertas >= max_pos:
                logger.info(
                    "Limite de posiciones alcanzado (%d/%d). Señales restantes descartadas.",
                    abiertas, max_pos,
                )
                break

            q = signal.pricing.quote
            logger.info(
                "Ejecutando señal: %s %s %s x%d @ %.2f | %s",
                signal.lado, q.tipo, q.simbolo,
                signal.cantidad, signal.precio_limite, signal.razon,
            )

            await self._oms.open_position(
                simbolo       = q.simbolo,
                tipo          = q.tipo,
                lado          = signal.lado,
                cantidad      = signal.cantidad,
                precio_limite = signal.precio_limite,
            )
            abiertas += 1

    async def on_snapshot(self, snapshot: MarketSnapshot) -> None:
        """
        Callback para MarketDataFeed. En cada tick:
        1. Enriquece el snapshot con precios BSM e IV
        2. Evalúa oportunidades de mispricing
        3. Ejecuta las señales encontradas
        """
        pricings = enrich_snapshot(snapshot, r=DEFAULT_RISK_FREE_RATE)
        if not pricings:
            return

        signals, _ = self.evaluar(pricings)

        if signals:
            logger.info(
                "Snapshot evaluado: %d señales sobre %d opciones analizadas.",
                len(signals), len(pricings),
            )
            await self.ejecutar_signals(signals)
        else:
            logger.debug(
                "Snapshot evaluado: sin señales (%d opciones analizadas).",
                len(pricings),
            )

    # ── Evaluación individual ─────────────────────────────────────────────

    def _evaluar_uno(self, p: OptionPricing) -> tuple[TradeSignal | None, str | None]:
        q   = p.quote
        g   = p.greeks
        cfg = self._config

        # 1. Liquidez: bid y ask deben existir (fuera de rueda son null)
        if q.bid is None or q.ask is None:
            return None, "sin_puntas"

        # 2. Spread dentro del rango aceptable
        spread = q.spread_pct
        if spread is None:
            return None, "spread"
        if not (cfg.min_spread_pct <= spread <= cfg.max_spread_pct):
            return None, "spread"

        # 3. DTE dentro del rango objetivo
        dte = q.dias_al_vencimiento
        if not (cfg.min_dte <= dte <= cfg.max_dte):
            return None, "dte"

        # 4. IV debe ser calculable
        if g.iv is None:
            return None, "sin_iv"

        # 5. Delta dentro del rango (evitar deep OTM / deep ITM)
        delta_abs = abs(g.delta)
        if not (cfg.min_delta_abs <= delta_abs <= cfg.max_delta_abs):
            return None, "delta"

        # 6. Mispricing suficiente para cubrir costos de transacción
        mispricing = p.mispricing
        if mispricing is None or g.price <= 0:
            return None, "mispricing"

        mispricing_pct = mispricing / g.price

        if mispricing_pct > cfg.min_mispricing_pct:
            # Mercado sobrevalora -> vender (SHORT), precio al bid
            return TradeSignal(
                pricing       = p,
                lado          = "SHORT",
                precio_limite = q.bid,
                cantidad      = cfg.lote_base,
                razon         = (
                    f"IV={g.iv:.1%} BSM={g.price:.2f} mid={q.mid:.2f} "
                    f"misprice={mispricing_pct:+.1%} DTE={dte}"
                ),
                score = mispricing_pct,
            ), None

        if mispricing_pct < -cfg.min_mispricing_pct:
            # Mercado subvalora -> comprar (LONG), precio al ask
            return TradeSignal(
                pricing       = p,
                lado          = "LONG",
                precio_limite = q.ask,
                cantidad      = cfg.lote_base,
                razon         = (
                    f"IV={g.iv:.1%} BSM={g.price:.2f} mid={q.mid:.2f} "
                    f"misprice={mispricing_pct:+.1%} DTE={dte}"
                ),
                score = abs(mispricing_pct),
            ), None

        # Near-miss: mispricing está cerca del umbral (>80% del mínimo)
        if abs(mispricing_pct) > cfg.min_mispricing_pct * 0.8:
            logger.info(
                "  🔸 Near-miss %s: misprice=%.2f%% (umbral=%.2f%%) IV=%.1f%% DTE=%d",
                q.simbolo, mispricing_pct * 100, cfg.min_mispricing_pct * 100,
                g.iv * 100, dte,
            )
            return None, "near_miss"

        return None, "mispricing"
