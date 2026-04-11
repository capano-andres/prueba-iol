"""
Módulo 5b: Estrategia Bull Call Spread Direccional

Basada en el análisis cuantitativo de COME (estrategia-come.md):
  - Compra un Call cercano al spot (ATM o ligeramente OTM)
  - Vende un Call más alejado (OTM, ancho configurable)
  - Mismo vencimiento para ambas patas
  - Gestiona el par como una unidad (SpreadPosition)

Ventajas vs Long Call puro:
  - La prima de la pata vendida subsidia el costo de la pata comprada
  - Neutraliza parcialmente el deterioro temporal (Theta)
  - Mitiga colapsos de volatilidad implícita (Vega)
  - ROIC stratosférico si el subyacente cierra por encima del strike superior

Optimización tarifaria IOL:
  - force_intraday_close=True: cierra antes de las 16:45 para bonificar el 100%
    de la comisión IOL en la pata de cierre (solo paga Derechos ByMA 0.20%)
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
class BullSpreadConfig:
    """
    Parámetros de la estrategia Bull Call Spread.

    Ajustados para COME (~48 ARS) pero funcionan con cualquier activo.
    """

    # ── Construcción del spread ───────────────────────────────────────────
    strike_width_pct: float = 0.12
    """
    Ancho del spread como % del spot.
    Ej: spot=48, width=12% → long_strike≈48, short_strike≈53.76
    Rango útil para COME dado el rango técnico $48–$55 documentado.
    """

    atm_offset_pct: float = 0.00
    """
    Desplazamiento de la pata comprada respecto al spot.
    0.0  = ATM (strike = spot)
    0.05 = OTM 5% (strike = spot × 1.05)
    Preferir 0 para mayor delta y menor prima.
    """

    max_net_premium_pct: float = 0.08
    """
    Prima neta máxima aceptable como % del spot.
    8% = 3.89 ARS con spot=$48.59 — cubre el rango ATM real de COME.
    Reducir a 4-5% en mercados de IV baja para exigir spreads más baratos.
    """

    # ── Filtros temporales ────────────────────────────────────────────────
    min_dte: int = 10
    """DTE mínimo. Evitar vencimientos próximos con Gamma extremo no controlado."""

    max_dte: int = 75
    """DTE máximo. El documento analiza vencimientos Junio 2026 (~69 DTE).
    Ampliado a 75 para incluir esos vencimientos. Opciones más largas tienen
    mayor prima; el spread sigue siendo eficiente si el ancho lo justifica.
    """

    # ── Filtros de liquidez ───────────────────────────────────────────────
    max_spread_pct: float = 0.25
    """Spread bid-ask máximo. Filtrar opciones ilíquidas donde la fricción destruye el edge."""

    min_spread_pct: float = 0.01
    """Spread bid-ask mínimo. Bid=Ask exacto a veces indica precios teóricos sin mercado real."""

    # ── Gestión de riesgo y posición ─────────────────────────────────────
    lotes_por_spread: int = 1
    """Contratos por cada pata del spread."""

    max_spreads_abiertos: int = 3
    """Máximo de spreads simultáneos. Limita la exposición total."""

    stop_loss_pct: float = 0.80
    """
    Cerrar spread si la prima neta pierde este % de su valor inicial.
    Ej: 0.80 → cerrar si la pérdida supera el 80% de la prima pagada.
    """

    take_profit_pct: float = 0.65
    """
    Cerrar spread al capturar este % de la ganancia máxima teórica.
    Ej: 0.65 → cerrar al alcanzar el 65% de (ancho_spread - prima_neta).
    Más conservador que esperar al vencimiento; elimina el riesgo overnight.
    """

    # ── Control intradiario ───────────────────────────────────────────────
    force_intraday_close: bool = False
    """
    Forzar cierre de todas las posiciones antes de las 16:45 ARG.
    Cuando es True:
      1. Invoca la bonificación 100% comisión IOL en la pata de cierre.
      2. Evita ejercicio automático ByMA Clearing de opciones ITM al vencimiento.
    Por defecto False: el usuario controla el cierre manualmente.
    Activar si se quiere automatizar el cierre intradiario.
    """

    min_reward_risk_ratio: float = 0.8
    """
    Ratio mínimo beneficio_máximo / prima_neta para abrir el spread.
    0.8 es conservador pero realista con spreads ATM sobre activos arg. de baja IV.
    Subir a 1.5+ para mercados con IV alta donde el spread sea más barato.
    """


# ─── Señal de spread ─────────────────────────────────────────────────────────

@dataclass
class SpreadSignal:
    """Señal de apertura de un Bull Call Spread."""
    long_leg:        OptionPricing      # Call comprado (ATM/OTM bajo)
    short_leg:       OptionPricing      # Call vendido  (OTM alto)
    net_premium:     float              # Prima neta (pago = ask_long - bid_short)
    max_profit:      float              # Ganancia máxima (ancho - prima)
    max_loss:        float              # Pérdida máxima (= prima neta)
    breakeven:       float              # Strike_long + prima_neta
    reward_risk:     float              # max_profit / max_loss
    expiry:          date
    score:           float              # Mayor = más atractivo (= reward_risk)

    @property
    def informacion(self) -> str:
        return (
            f"LONG {self.long_leg.quote.simbolo} (K={self.long_leg.quote.strike:.1f}) "
            f"+ SHORT {self.short_leg.quote.simbolo} (K={self.short_leg.quote.strike:.1f}) | "
            f"prima_neta={self.net_premium:.2f} "
            f"max_profit={self.max_profit:.2f} "
            f"R/R={self.reward_risk:.2f} "
            f"BEP={self.breakeven:.2f} "
            f"DTE={self.long_leg.quote.dias_al_vencimiento}"
        )


# ─── Posición de spread (par de posiciones vinculadas) ───────────────────────

@dataclass
class SpreadPosition:
    """
    Representa un Bull Call Spread abierto:
    dos órdenes (patas) tratadas como una unidad.
    """
    group_id:       str
    long_pos_id:    str     # ID de la Position en OMS (pata larga)
    short_pos_id:   str     # ID de la Position en OMS (pata corta)
    signal:         SpreadSignal
    net_premium_paid: float
    is_open:        bool = True
    status:         str  = "PENDING"  # PENDING | OPEN | CLOSED
    long_order_id:  str  = ""
    short_order_id: str  = ""

    @property
    def max_gain(self) -> float:
        return self.signal.max_profit

    @property
    def stop_loss_threshold(self) -> float:
        """Prima neta que activa el stop loss."""
        return self.net_premium_paid

    def should_stop_loss(self, current_net_value: float, config: BullSpreadConfig) -> bool:
        """
        Cierra si el valor actual del spread cayó más del stop_loss_pct
        respecto a la prima pagada (pérdida no realizada >= umbral).
        """
        loss = self.net_premium_paid - current_net_value
        return loss / self.net_premium_paid >= config.stop_loss_pct

    def should_take_profit(self, current_net_value: float, config: BullSpreadConfig) -> bool:
        """
        Cierra si la ganancia capturada >= take_profit_pct × max_profit.
        """
        gain = current_net_value - self.net_premium_paid
        return gain >= config.take_profit_pct * self.max_gain


# ─── Estrategia ───────────────────────────────────────────────────────────────

class BullCallSpreadStrategy:
    """
    Motor de señales para la estrategia Bull Call Spread direccional alcista.

    Lógica central:
      1. En cada snapshot, enriquece las opciones con griegas BSM.
      2. Filtra calls por DTE, liquidez e IV.
      3. Para cada vencimiento, busca pares (long_leg, short_leg) con
         el ancho de strike configurado y calcula la prima neta.
      4. Genera SpreadSignals ordenadas por reward/risk ratio.
      5. Abre los spreads más atractivos respetando el límite de posiciones.
      6. Monitorea posiciones abiertas aplicando stop-loss y take-profit.
      7. Fuerza cierre intradiario antes del cutoff si force_intraday_close=True.

    Uso típico (análogo a Strategy):
        bcs = BullCallSpreadStrategy(oms)
        feed.on_snapshot(bcs.on_snapshot)
    """

    def __init__(self, oms: "OMS", config: BullSpreadConfig | None = None) -> None:
        self._oms    = oms
        self._config = config or BullSpreadConfig()
        self._open_spreads: dict[str, SpreadPosition] = {}  # group_id → SpreadPosition
        self._counter = 0   # para generar group_ids únicos

    # ── API pública ───────────────────────────────────────────────────────

    def evaluar(
        self,
        pricings: list[OptionPricing],
        spot: float,
    ) -> list[SpreadSignal]:
        """
        Evalúa el snapshot y retorna SpreadSignals ordenadas por score.
        No abre ninguna posición — solo analiza.
        """
        calls = [p for p in pricings if p.quote.tipo == "CALL"]
        if not calls or spot <= 0:
            return []

        # Agrupar por vencimiento
        by_expiry: dict[date, list[OptionPricing]] = {}
        for p in calls:
            q = p.quote
            # Filtros de liquidez y tiempo
            if not self._pasa_filtros(q):
                continue
            by_expiry.setdefault(q.expiry, []).append(p)

        signals: list[SpreadSignal] = []
        for expiry, expiry_calls in by_expiry.items():
            # Ordenar por strike
            expiry_calls.sort(key=lambda p: p.quote.strike)
            found = self._buscar_spreads(expiry_calls, spot)
            signals.extend(found)

        signals.sort(key=lambda s: s.score, reverse=True)
        return signals

    async def on_snapshot(self, snapshot: MarketSnapshot) -> None:
        """
        Callback para MarketDataFeed. En cada tick:
          1. Enriquece snapshot con BSM/IV
          2. Monitorea spreads abiertos (SL/TP)
          3. Evalúa y abre nuevos spreads si hay cupo
        """
        if snapshot.spot is None or snapshot.spot <= 0:
            return

        pricings = enrich_snapshot(snapshot, r=DEFAULT_RISK_FREE_RATE)
        if not pricings:
            return

        # Monitorear spreads existentes
        await self._monitorear_spreads_abiertos(snapshot, pricings)

        # Evaluar nuevas oportunidades
        n_abiertos = sum(1 for s in self._open_spreads.values() if s.is_open)
        if n_abiertos >= self._config.max_spreads_abiertos:
            logger.debug(
                "BullSpread: límite de spreads alcanzado (%d/%d).",
                n_abiertos, self._config.max_spreads_abiertos,
            )
            return

        signals = self.evaluar(pricings, snapshot.spot)
        if not signals:
            logger.debug("BullSpread: sin señales en este snapshot.")
            return

        logger.info(
            "BullSpread: %d señales encontradas. Top: %s",
            len(signals), signals[0].informacion,
        )

        await self._ejecutar_signals(signals, snapshot)

    # ── Evaluación interna ────────────────────────────────────────────────

    def _pasa_filtros(self, q: OptionQuote) -> bool:
        """Filtros de liquidez y tiempo aplicados a cada Call individual."""
        # Bid/ask deben existir (fuera de rueda son None)
        if q.bid is None or q.ask is None:
            return False

        # Spread bid-ask dentro del rango aceptable
        spread = q.spread_pct
        if spread is None:
            return False
        if not (self._config.min_spread_pct <= spread <= self._config.max_spread_pct):
            return False

        # DTE dentro del rango objetivo
        dte = q.dias_al_vencimiento
        if not (self._config.min_dte <= dte <= self._config.max_dte):
            return False

        return True

    def _buscar_spreads(
        self,
        calls: list[OptionPricing],  # ya filtrados, ordenados por strike ascendente
        spot: float,
    ) -> list[SpreadSignal]:
        """
        Busca pares (long, short) que cumplan el criterio de ancho de spread.
        Para cada call candidato a pata larga, busca el short más cercano
        al strike objetivo (spot × (1 + atm_offset + strike_width)).
        """
        cfg = self._config
        signals = []

        target_long_strike = spot * (1.0 + cfg.atm_offset_pct)
        target_short_strike = target_long_strike * (1.0 + cfg.strike_width_pct)
        max_premium = spot * cfg.max_net_premium_pct

        for i, long_p in enumerate(calls):
            long_q = long_p.quote
            # La pata larga debe estar cerca del strike objetivo (± 15%)
            if abs(long_q.strike - target_long_strike) / target_long_strike > 0.15:
                continue
            # La pata larga no puede estar demasiado OTM (delta mínimo útil)
            if long_p.greeks.iv is None:
                continue
            if abs(long_p.greeks.delta) < 0.10:
                continue

            # Buscar la mejor pata corta
            for short_p in calls[i + 1:]:
                short_q = short_p.quote
                if short_q.strike <= long_q.strike:
                    continue
                # El strike corto debe estar cerca del objetivo
                if abs(short_q.strike - target_short_strike) / target_short_strike > 0.20:
                    continue

                # Calcular prima neta (costo del spread):
                # Pagamos ask de la pata larga, cobramos bid de la pata corta
                ask_long  = long_q.ask
                bid_short = short_q.bid
                if ask_long is None or bid_short is None:
                    continue
                if bid_short <= 0:
                    # No hay comprador para la pata corta → spread costoso
                    continue

                net_premium = ask_long - bid_short
                if net_premium <= 0:
                    # El spread se puede construir con crédito neto — inusual,
                    # pero si ocurre es una señal muy fuerte; incluir con score alto.
                    net_premium = 0.01  # evitar división por cero

                if net_premium > max_premium:
                    continue

                ancho = short_q.strike - long_q.strike
                max_profit = ancho - net_premium
                if max_profit <= 0:
                    continue

                reward_risk = max_profit / net_premium
                if reward_risk < cfg.min_reward_risk_ratio:
                    continue

                breakeven = long_q.strike + net_premium

                signals.append(SpreadSignal(
                    long_leg      = long_p,
                    short_leg     = short_p,
                    net_premium   = net_premium,
                    max_profit    = max_profit,
                    max_loss      = net_premium,
                    breakeven     = breakeven,
                    reward_risk   = reward_risk,
                    expiry        = long_q.expiry,
                    score         = reward_risk,
                ))

        return signals

    # ── Ejecución ─────────────────────────────────────────────────────────

    async def _ejecutar_signals(
        self,
        signals: list[SpreadSignal],
        snapshot: MarketSnapshot,
    ) -> None:
        """Abre spreads en orden de score hasta completar el límite."""
        n_abiertos = sum(1 for s in self._open_spreads.values() if s.is_open)
        max_pos = self._config.max_spreads_abiertos

        for signal in signals:
            if n_abiertos >= max_pos:
                break

            # Verificar que no tenemos ya este par de strikes abierto
            if self._ya_abierto(signal):
                continue

            spread_pos = await self._abrir_spread(signal)
            if spread_pos:
                n_abiertos += 1

    async def _abrir_spread(self, signal: SpreadSignal) -> SpreadPosition | None:
        """
        Abre las dos patas del spread via OMS.
        Pata larga primero (compra), luego pata corta (venta).
        """
        cfg = self._config
        long_q  = signal.long_leg.quote
        short_q = signal.short_leg.quote

        logger.info("BullSpread: Abriendo spread → %s", signal.informacion)

        # Pata larga (compra Call)
        long_order = await self._oms.open_position(
            simbolo       = long_q.simbolo,
            tipo          = "CALL",
            lado          = "LONG",
            cantidad      = cfg.lotes_por_spread,
            precio_limite = long_q.ask,  # pagamos el ask
        )

        if long_order.estado.value in ("rechazada", "cancelada"):
            logger.warning(
                "BullSpread: Pata larga rechazada (%s). Spread abortado.",
                long_order.estado.value,
            )
            return None

        # Pata corta (venta Call)
        short_order = await self._oms.open_position(
            simbolo       = short_q.simbolo,
            tipo          = "CALL",
            lado          = "SHORT",
            cantidad      = cfg.lotes_por_spread,
            precio_limite = short_q.bid,  # cobramos el bid
        )

        if short_order.estado.value in ("rechazada", "cancelada"):
            logger.warning(
                "BullSpread: Pata corta rechazada/cancelada (%s). Iniciando ROLLBACK de pata larga...",
                short_order.estado.value,
            )
            if long_order.estado.value == "pendiente":
                await self._oms.cancel_order(long_order.local_id)
            else:
                long_pos_id_rb = self._ultimo_pos_id(long_q.simbolo)
                if long_pos_id_rb:
                    precio_rb = long_q.bid or long_q.ask or long_q.ultimo or 0.01
                    try:
                        await self._oms.close_position(long_pos_id_rb, precio_limite=precio_rb)
                        logger.info("BullSpread ROLLBACK: Pata larga cerrada (precio=%.2f).", precio_rb)
                    except Exception as exc:
                        logger.error("BullSpread ROLLBACK FALLIDO: %s", exc)
            return None

        self._counter += 1
        group_id = f"bcs_{self._counter:04d}"

        # Al principio no tenemos pos_ids porque están pendientes. Se las asignamos vacías y las llenará el monitor.
        from oms import OrderStatus
        is_instantly_open = (long_order.estado == OrderStatus.EJECUTADA and short_order.estado == OrderStatus.EJECUTADA)

        spread = SpreadPosition(
            group_id         = group_id,
            long_pos_id      = self._ultimo_pos_id(long_q.simbolo) if long_order.estado == OrderStatus.EJECUTADA else "",
            short_pos_id     = self._ultimo_pos_id(short_q.simbolo) if short_order.estado == OrderStatus.EJECUTADA else "",
            signal           = signal,
            net_premium_paid = signal.net_premium,
            status           = "OPEN" if is_instantly_open else "PENDING",
            long_order_id    = long_order.local_id,
            short_order_id   = short_order.local_id,
        )
        self._open_spreads[group_id] = spread

        logger.info(
            "BullSpread [%s]: Spread %s | prima_neta=%.2f | max_profit=%.2f | BEP=%.2f",
            group_id, spread.status, signal.net_premium, signal.max_profit, signal.breakeven,
        )
        return spread

    # ── Monitoreo de posiciones ───────────────────────────────────────────

    async def _monitorear_spreads_abiertos(
        self,
        snapshot: MarketSnapshot,
        pricings: list[OptionPricing],
    ) -> None:
        """
        Revisa cada spread abierto:
          - Aplica stop-loss y take-profit
          - Cierra si force_intraday_close y el OMS está en pre-cierre
        """
        # Mapa rápido símbolo → mid
        mid_map: dict[str, float] = {}
        for p in pricings:
            m = p.quote.mid
            if m:
                mid_map[p.quote.simbolo] = m

        for group_id, spread in list(self._open_spreads.items()):
            if not spread.is_open:
                continue

            # --- Resolución de Spreads Pendientes (ANTI-LEGGING) ---
            if spread.status == "PENDING":
                l_ord = self._oms._orders.get(spread.long_order_id)
                s_ord = self._oms._orders.get(spread.short_order_id)
                if not l_ord or not s_ord:
                    continue
                
                # Check if both filled
                from oms import OrderStatus
                if l_ord.estado == OrderStatus.EJECUTADA and s_ord.estado == OrderStatus.EJECUTADA:
                    spread.status = "OPEN"
                    spread.long_pos_id = self._find_pos_by_order(l_ord.local_id) or ""
                    spread.short_pos_id = self._find_pos_by_order(s_ord.local_id) or ""
                    logger.info("BullSpread [%s]: Spread PENDIENTE -> OPEN (Ejecutado al fin)", group_id)
                    continue

                # Check if anyone is cancelled (e.g. by 180s timeout or market reject)
                l_fail = l_ord.estado in (OrderStatus.CANCELADA, OrderStatus.RECHAZADA)
                s_fail = s_ord.estado in (OrderStatus.CANCELADA, OrderStatus.RECHAZADA)
                
                if l_fail and s_fail:
                    spread.status = "CLOSED"
                    spread.is_open = False
                    logger.info("BullSpread [%s]: PENDIENTE -> CANCELADO (Ambas patas expiraron/rechazadas)", group_id)
                    continue
                elif l_fail and s_ord.estado == OrderStatus.EJECUTADA:
                    # Legging risk: short was filled, long was cancelled! Must close Short leg at market!
                    pos_id = self._find_pos_by_order(s_ord.local_id)
                    if pos_id:
                        mid = mid_map.get(s_ord.simbolo) or s_ord.precio_limite
                        logger.warning("BullSpread [%s]: ANTI-LEGGING: Pata compra cancelada pero Venta ejecutada. Liquidando Venta pos %s", group_id, pos_id)
                        await self._oms.close_position(pos_id, precio_limite=mid)
                    spread.status = "CLOSED"
                    spread.is_open = False
                    continue
                elif s_fail and l_ord.estado == OrderStatus.EJECUTADA:
                    # Legging risk: long filled, short cancelled!
                    pos_id = self._find_pos_by_order(l_ord.local_id)
                    if pos_id:
                        mid = mid_map.get(l_ord.simbolo) or l_ord.precio_limite
                        logger.warning("BullSpread [%s]: ANTI-LEGGING: Pata Venta cancelada pero Compra ejecutada. Liquidando Compra pos %s", group_id, pos_id)
                        await self._oms.close_position(pos_id, precio_limite=mid)
                    spread.status = "CLOSED"
                    spread.is_open = False
                    continue
                else:
                    # Aún PENDIENTE (esperando fill)
                    continue

            # --- Spreads Activos (OPEN) ---

            long_signal  = spread.signal.long_leg.quote
            short_signal = spread.signal.short_leg.quote

            mid_long  = mid_map.get(long_signal.simbolo)
            mid_short = mid_map.get(short_signal.simbolo)

            if mid_long is None or mid_short is None:
                continue  # sin datos de precio; no actuar

            # Valor actual del spread (recibir si vendemos long, pagar si compramos short)
            current_net_value = mid_long - mid_short

            razon: str | None = None
            if spread.should_stop_loss(current_net_value, self._config):
                razon = f"STOP-LOSS (valor={current_net_value:.2f} < umbral)"
            elif spread.should_take_profit(current_net_value, self._config):
                razon = f"TAKE-PROFIT (valor={current_net_value:.2f})"

            if razon:
                logger.info(
                    "BullSpread [%s]: Cerrando por %s", group_id, razon,
                )
                await self._cerrar_spread(spread, snapshot)

    async def _cerrar_spread(
        self,
        spread: SpreadPosition,
        snapshot: MarketSnapshot,
    ) -> None:
        """Cierra ambas patas del spread."""
        # Mapa mid
        mid_map = {
            opt.simbolo: opt.mid
            for opt in snapshot.opciones
            if opt.mid
        }

        # Precio de cierre: mid del mercado o fallback al precio de apertura
        precio_long  = mid_map.get(spread.signal.long_leg.quote.simbolo) or spread.signal.long_leg.quote.ask
        precio_short = mid_map.get(spread.signal.short_leg.quote.simbolo) or spread.signal.short_leg.quote.bid

        if precio_long:
            await self._oms.close_position(spread.long_pos_id, precio_limite=precio_long)

        if spread.short_pos_id and precio_short:
            await self._oms.close_position(spread.short_pos_id, precio_limite=precio_short)

        spread.is_open = False
        logger.info("BullSpread [%s]: Spread CERRADO.", spread.group_id)

    async def cerrar_todos(self, snapshot: MarketSnapshot) -> None:
        """Cierra todos los spreads abiertos. Llamar al fin de la rueda."""
        abiertos = [s for s in self._open_spreads.values() if s.is_open]
        if not abiertos:
            return
        logger.warning(
            "BullSpread: Cerrando %d spreadss al fin de la rueda (force_intraday_close)...",
            len(abiertos),
        )
        for spread in abiertos:
            await self._cerrar_spread(spread, snapshot)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _ya_abierto(self, signal: SpreadSignal) -> bool:
        """Evitar duplicar un spread con el mismo par de strikes."""
        long_strike  = signal.long_leg.quote.strike
        short_strike = signal.short_leg.quote.strike
        expiry       = signal.expiry
        for s in self._open_spreads.values():
            if not s.is_open:
                continue
            if (
                s.signal.long_leg.quote.strike  == long_strike
                and s.signal.short_leg.quote.strike == short_strike
                and s.signal.expiry == expiry
            ):
                return True
        return False

    def _ultimo_pos_id(self, simbolo: str) -> str | None:
        """
        Encuentra el ID de la última Position abierta en el OMS con ese símbolo.
        Busca en las posiciones abiertas del OMS en orden inverso de creación.
        """
        from oms import PositionStatus
        abiertas = [
            p for p in self._oms._positions.values()
            if p.simbolo == simbolo and p.estado == PositionStatus.ABIERTA
        ]
        if not abiertas:
            return None
        # La más reciente tiene la fecha de apertura más alta
        return max(abiertas, key=lambda p: p.fecha_apertura).id

    def _find_pos_by_order(self, order_local_id: str) -> str | None:
        """Encuentra la Position generada por una order_apertura."""
        for p in self._oms._positions.values():
            if p.order_apertura == order_local_id:
                return p.id
        return None

    # ── Métricas ─────────────────────────────────────────────────────────

    def resumen_spreads(self) -> list[dict]:
        """Serializa los spreads abiertos para la API/UI."""
        resultado = []
        for s in self._open_spreads.values():
            resultado.append({
                "group_id":       s.group_id,
                "is_open":        s.is_open,
                "long_simbolo":   s.signal.long_leg.quote.simbolo,
                "short_simbolo":  s.signal.short_leg.quote.simbolo,
                "long_strike":    s.signal.long_leg.quote.strike,
                "short_strike":   s.signal.short_leg.quote.strike,
                "expiry":         str(s.signal.expiry),
                "net_premium":    round(s.net_premium_paid, 2),
                "max_profit":     round(s.max_gain, 2),
                "breakeven":      round(s.signal.breakeven, 2),
                "reward_risk":    round(s.signal.reward_risk, 2),
                "dte":            s.signal.long_leg.quote.dias_al_vencimiento,
            })
        return resultado
