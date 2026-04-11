"""
Módulo 4: Order Management System (OMS) Intradiario
- Colocación y cancelación de órdenes limitadas via IOL API
- Seguimiento de posiciones abiertas y ciclos intradiarios
- Modelo de comisiones IOL + Derechos ByMA (bonificación intradiaria)
- Cierre automático antes del fin de la rueda (riesgo de ejercicio ITM)
- Modo dry-run por defecto (no envía órdenes reales)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timezone, timedelta
from enum import Enum
from typing import Callable

from iol_client import IOLClient, IOLRequestError
from market_data import MarketSnapshot
from math_engine import OptionPricing

logger = logging.getLogger(__name__)

# ─── Horario de mercado (ByMA, zona Argentina UTC-3) ─────────────────────────

_TZ_ARG          = timezone(timedelta(hours=-3))   # Argentina UTC-3, sin DST
_MARKET_OPEN     = time(11, 0)
_MARKET_CLOSE    = time(17, 0)
_PRECLOSE_CUTOFF = time(16, 45)   # a partir de aquí se cierran posiciones abiertas

# ─── Modelo de comisiones IOL ─────────────────────────────────────────────────

class IOLProfile(Enum):
    """Perfil de comisión según volumen mensual operado."""
    GOLD     = 0.0050   # < $7.5M  →  0.50 %
    PLATINUM = 0.0030   # $7.5M–$50M  →  0.30 %
    BLACK    = 0.0010   # > $50M  →  0.10 %

_BYMA_RIGHTS = 0.0020   # Derechos ByMA (fijo, opciones)
_IVA         = 0.21     # IVA sobre comisiones

def calc_commission(
    nominal:         float,
    profile:         IOLProfile = IOLProfile.GOLD,
    intraday_close:  bool = False,
) -> float:
    """
    Calcula la comisión total de una pata de la operación.

    Pata de apertura  : comisión IOL + Derechos ByMA + IVA
    Pata de cierre intradiario : solo Derechos ByMA + IVA  (IOL 100 % bonificado)
    """
    byma    = nominal * _BYMA_RIGHTS
    iol     = 0.0 if intraday_close else nominal * profile.value
    subtotal = iol + byma
    return subtotal + subtotal * _IVA


# ─── Estados de orden y posición ─────────────────────────────────────────────

class OrderStatus(Enum):
    PENDIENTE    = "pendiente"
    EJECUTADA    = "ejecutada"
    PARCIAL      = "parcial"
    CANCELADA    = "cancelada"
    RECHAZADA    = "rechazada"

class PositionStatus(Enum):
    ABIERTA  = "abierta"
    CERRADA  = "cerrada"


# ─── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class Order:
    """Representa una orden enviada (o simulada en dry-run) al mercado."""
    local_id:         str             = field(default_factory=lambda: str(uuid.uuid4())[:8])
    remote_id:        int | None      = None          # ID asignado por IOL
    simbolo:          str             = ""
    operacion:        str             = ""            # "compra" | "venta"
    cantidad:         int             = 0
    precio_limite:    float           = 0.0
    plazo:            str             = "t0"
    estado:           OrderStatus     = OrderStatus.PENDIENTE
    precio_ejecucion: float | None    = None
    cantidad_ejec:    int             = 0
    ts_envio:         datetime        = field(default_factory=datetime.now)
    ts_ejecucion:     datetime | None = None
    dry_run:          bool            = True

    @property
    def nominal(self) -> float:
        precio = self.precio_ejecucion or self.precio_limite
        return precio * (self.cantidad_ejec or self.cantidad)


@dataclass
class Position:
    """Posición abierta o cerrada, con trazabilidad intradiaria."""
    id:                str          = field(default_factory=lambda: str(uuid.uuid4())[:8])
    simbolo:           str          = ""
    tipo:              str          = ""    # "CALL" | "PUT"
    lado:              str          = ""    # "LONG" | "SHORT"
    cantidad:          int          = 0
    precio_apertura:   float        = 0.0
    comision_apertura: float        = 0.0
    fecha_apertura:    datetime     = field(default_factory=datetime.now)
    order_apertura:    str          = ""    # local_id de la orden de apertura
    precio_cierre:     float | None = None
    comision_cierre:   float | None = None
    order_cierre:      str | None   = None
    estado:            PositionStatus = PositionStatus.ABIERTA

    # ── Métricas ─────────────────────────────────────────────────────────

    @property
    def costo_total(self) -> float:
        return self.precio_apertura * self.cantidad + self.comision_apertura

    @property
    def pnl_realizado(self) -> float | None:
        """P&L neto (sin comisiones ya incluidas en costo)."""
        if self.precio_cierre is None or self.comision_cierre is None:
            return None
        ingreso = self.precio_cierre * self.cantidad
        if self.lado == "LONG":
            return ingreso - self.precio_apertura * self.cantidad \
                   - self.comision_apertura - self.comision_cierre
        # SHORT: vendemos para abrir, compramos para cerrar
        return self.precio_apertura * self.cantidad \
               - ingreso - self.comision_apertura - self.comision_cierre

    def pnl_no_realizado(self, precio_mercado: float) -> float:
        if self.lado == "LONG":
            return (precio_mercado - self.precio_apertura) * self.cantidad
        return (self.precio_apertura - precio_mercado) * self.cantidad

    def es_intraday(self) -> bool:
        return self.fecha_apertura.date() == datetime.now().date()


# ─── OMS ─────────────────────────────────────────────────────────────────────

class OMS:
    """
    Order Management System intradiario para opciones GGAL.

    Uso recomendado
    ---------------
    oms = OMS(client, profile=IOLProfile.GOLD, dry_run=True)
    # conectar al feed:
    feed.on_snapshot(oms.on_snapshot)
    # abrir posición desde estrategia:
    order = await oms.open_position("GFGC71487A", "CALL", lado="LONG",
                                    cantidad=1, precio_limite=122.0)
    # cierre manual o automático al final de la rueda
    """

    def __init__(
        self,
        client:       IOLClient,
        mercado:      str            = "bCBA",
        profile:      IOLProfile     = IOLProfile.GOLD,
        dry_run:      bool           = True,
        max_nominal:  float | None   = None,
    ) -> None:
        self._client  = client
        self._mercado = mercado
        self._profile = profile
        self._dry_run = dry_run
        self._max_nominal = max_nominal   # límite de fondos asignados (None = sin límite)

        self._orders:    dict[str, Order]    = {}   # local_id → Order
        self._positions: dict[str, Position] = {}   # pos_id   → Position

        self._auto_close_task: asyncio.Task | None = None
        self._on_fill_callbacks: list[Callable] = []

        mode = "DRY-RUN" if dry_run else "LIVE"
        logger.info("OMS iniciado [%s] — perfil %s, mercado %s.", mode, profile.name, mercado)

    # ── Propiedades de consulta ───────────────────────────────────────────

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def posiciones_abiertas(self) -> list[Position]:
        return [p for p in self._positions.values() if p.estado == PositionStatus.ABIERTA]

    def posiciones_intraday(self) -> list[Position]:
        return [p for p in self.posiciones_abiertas() if p.es_intraday()]

    def ordenes_pendientes(self) -> list[Order]:
        return [o for o in self._orders.values() if o.estado == OrderStatus.PENDIENTE]

    @property
    def nominal_en_uso(self) -> float:
        """Nominal total de posiciones abiertas."""
        return sum(
            p.precio_apertura * p.cantidad
            for p in self.posiciones_abiertas()
        )

    def pnl_realizado_total(self) -> float:
        total = 0.0
        for p in self._positions.values():
            r = p.pnl_realizado
            if r is not None:
                total += r
        return total

    # ── Apertura de posición ──────────────────────────────────────────────

    async def open_position(
        self,
        simbolo:       str,
        tipo:          str,          # "CALL" | "PUT"
        lado:          str,          # "LONG" (compra) | "SHORT" (venta)
        cantidad:      int,
        precio_limite: float,
        plazo:         str = "t0",
    ) -> Order:
        """
        Coloca una orden límite de apertura.
        En dry-run, simula llenado inmediato al precio límite.
        """
        operacion = "compra" if lado == "LONG" else "venta"
        order = Order(
            simbolo=simbolo,
            operacion=operacion,
            cantidad=cantidad,
            precio_limite=precio_limite,
            plazo=plazo,
            dry_run=self._dry_run,
        )

        nominal    = precio_limite * cantidad

        # Verificar límite de fondos asignados
        if self._max_nominal is not None:
            if self.nominal_en_uso + nominal > self._max_nominal:
                logger.warning(
                    "Orden rechazada: nominal %.2f excede fondos disponibles "
                    "(en uso: %.2f / max: %.2f)",
                    nominal, self.nominal_en_uso, self._max_nominal,
                )
                order.estado = OrderStatus.RECHAZADA
                self._orders[order.local_id] = order
                return order

        comision   = calc_commission(nominal, self._profile, intraday_close=False)

        if self._dry_run:
            # Simular ejecución inmediata
            order.estado           = OrderStatus.EJECUTADA
            order.precio_ejecucion = precio_limite
            order.cantidad_ejec    = cantidad
            order.ts_ejecucion     = datetime.now()
            logger.info("[DRY-RUN] Orden %s EJECUTADA: %s %s x%d @ %.2f",
                        order.local_id, operacion.upper(), simbolo, cantidad, precio_limite)
        else:
            try:
                resp = await self._client.place_order(
                    self._mercado, simbolo, operacion, cantidad, precio_limite, plazo
                )
                order.remote_id = resp.get("numeroOrden") or resp.get("id")
                logger.info("[LIVE] Orden %s enviada (remote=%s): %s %s x%d @ %.2f",
                            order.local_id, order.remote_id,
                            operacion.upper(), simbolo, cantidad, precio_limite)
            except IOLRequestError as exc:
                order.estado = OrderStatus.RECHAZADA
                logger.error("Error al enviar orden %s: %s", order.local_id, exc)
                self._orders[order.local_id] = order
                return order

        self._orders[order.local_id] = order

        # Crear posición si la orden fue ejecutada
        if order.estado == OrderStatus.EJECUTADA:
            pos = Position(
                simbolo=simbolo,
                tipo=tipo,
                lado=lado,
                cantidad=cantidad,
                precio_apertura=precio_limite,
                comision_apertura=comision,
                order_apertura=order.local_id,
            )
            self._positions[pos.id] = pos
            logger.info("Posicion abierta %s: %s %s x%d (comision=%.2f)",
                        pos.id, lado, simbolo, cantidad, comision)

        return order

    # ── Cierre de posición ────────────────────────────────────────────────

    async def close_position(
        self,
        pos_id:        str,
        precio_limite: float,
        plazo:         str = "t0",
    ) -> Order | None:
        """
        Cierra una posición abierta con una orden límite de sentido contrario.
        Aplica la bonificación intradiaria si la posición fue abierta hoy.
        """
        pos = self._positions.get(pos_id)
        if pos is None or pos.estado != PositionStatus.ABIERTA:
            logger.warning("close_position: posicion %s no encontrada o ya cerrada.", pos_id)
            return None

        operacion  = "venta" if pos.lado == "LONG" else "compra"
        intraday   = pos.es_intraday()
        nominal    = precio_limite * pos.cantidad
        comision   = calc_commission(nominal, self._profile, intraday_close=intraday)

        order = Order(
            simbolo=pos.simbolo,
            operacion=operacion,
            cantidad=pos.cantidad,
            precio_limite=precio_limite,
            plazo=plazo,
            dry_run=self._dry_run,
        )

        if self._dry_run:
            order.estado           = OrderStatus.EJECUTADA
            order.precio_ejecucion = precio_limite
            order.cantidad_ejec    = pos.cantidad
            order.ts_ejecucion     = datetime.now()
            bonus = " [INTRADAY — comision IOL bonificada]" if intraday else ""
            logger.info("[DRY-RUN] Cierre %s EJECUTADO: %s x%d @ %.2f | comision=%.2f%s",
                        order.local_id, pos.simbolo, pos.cantidad, precio_limite, comision, bonus)
        else:
            try:
                resp = await self._client.place_order(
                    self._mercado, pos.simbolo, operacion,
                    pos.cantidad, precio_limite, plazo,
                )
                order.remote_id = resp.get("numeroOrden") or resp.get("id")
                logger.info("[LIVE] Cierre enviado (remote=%s): %s x%d @ %.2f",
                            order.remote_id, pos.simbolo, pos.cantidad, precio_limite)
            except IOLRequestError as exc:
                order.estado = OrderStatus.RECHAZADA
                logger.error("Error al cerrar posicion %s: %s", pos_id, exc)
                self._orders[order.local_id] = order
                return order

        self._orders[order.local_id] = order

        if order.estado == OrderStatus.EJECUTADA:
            pos.precio_cierre   = precio_limite
            pos.comision_cierre = comision
            pos.order_cierre    = order.local_id
            pos.estado          = PositionStatus.CERRADA
            pnl = pos.pnl_realizado
            logger.info("Posicion %s CERRADA | P&L=%.2f ARS | intraday=%s",
                        pos.id, pnl or 0, intraday)

        return order

    # ── Cancelación de orden ──────────────────────────────────────────────

    async def cancel_order(self, local_id: str) -> bool:
        """Cancela una orden pendiente."""
        order = self._orders.get(local_id)
        if order is None or order.estado != OrderStatus.PENDIENTE:
            return False

        if self._dry_run:
            order.estado = OrderStatus.CANCELADA
            logger.info("[DRY-RUN] Orden %s cancelada.", local_id)
            return True

        if order.remote_id is None:
            return False
        try:
            await self._client.cancel_order(order.remote_id)
            order.estado = OrderStatus.CANCELADA
            logger.info("[LIVE] Orden %s (remote=%s) cancelada.", local_id, order.remote_id)
            return True
        except IOLRequestError as exc:
            logger.error("Error al cancelar orden %s: %s", local_id, exc)
            return False

    # ── Polling de órdenes (modo LIVE) ────────────────────────────────────

    async def poll_orders(self) -> None:
        """
        Consulta el estado de órdenes pendientes en IOL y actualiza posiciones.
        Llamar periódicamente en modo LIVE (ej: cada 5–10 s desde el feed).
        No hace nada en dry-run.
        """
        if self._dry_run:
            return

        pendientes = self.ordenes_pendientes()
        for order in pendientes:
            if order.remote_id is None:
                continue
            try:
                data = await self._client.get_order(order.remote_id)
                estado_api = str(data.get("estado") or "").lower()
                if estado_api in ("ejecutada", "filled", "terminada"):
                    order.estado           = OrderStatus.EJECUTADA
                    order.precio_ejecucion = float(data.get("precio") or order.precio_limite)
                    order.cantidad_ejec    = int(data.get("cantidad") or order.cantidad)
                    order.ts_ejecucion     = datetime.now()
                    logger.info("[LIVE] Orden %s ejecutada @ %.2f",
                                order.local_id, order.precio_ejecucion)
                elif estado_api in ("cancelada", "rechazada"):
                    order.estado = OrderStatus[estado_api.upper()]
            except IOLRequestError as exc:
                logger.warning("poll_orders: error consultando orden %s: %s",
                               order.remote_id, exc)

    # ── Cierre de emergencia (fin de rueda) ───────────────────────────────

    async def close_all_intraday(self, snapshot: MarketSnapshot | None = None) -> None:
        """
        Cierra todas las posiciones intradiarias abiertas.
        Si hay snapshot disponible, usa el mid como precio límite.
        Llamar manualmente o dejar que el scheduler lo dispare.
        """
        abiertas = self.posiciones_intraday()
        if not abiertas:
            logger.info("close_all_intraday: ninguna posicion intraday abierta.")
            return

        logger.warning("Cerrando %d posiciones intradiarias antes del cierre del mercado…",
                       len(abiertas))

        # Construir mapa simbolo → mid del snapshot
        mid_map: dict[str, float] = {}
        if snapshot:
            for opt in snapshot.opciones:
                if opt.mid:
                    mid_map[opt.simbolo] = opt.mid

        for pos in abiertas:
            precio = mid_map.get(pos.simbolo) or pos.precio_apertura  # fallback: al costo
            await self.close_position(pos.id, precio_limite=precio)

    # ── Auditoría ITM cerca del vencimiento ───────────────────────────────

    def audit_itm_risk(
        self,
        pricings: list[OptionPricing],
        spot:     float,
        dte_umbral: int = 1,
    ) -> list[Position]:
        """
        Identifica posiciones abiertas con opciones ITM a ≤ dte_umbral días.
        Estas posiciones serán ejercidas automáticamente por ByMA al vencimiento.
        Retorna la lista de posiciones en riesgo para que el caller las cierre.
        """
        # mapa simbolo → OptionPricing
        pricing_map = {p.quote.simbolo: p for p in pricings}

        en_riesgo: list[Position] = []
        for pos in self.posiciones_abiertas():
            pr = pricing_map.get(pos.simbolo)
            if pr is None:
                continue
            opt = pr.quote
            if opt.dias_al_vencimiento > dte_umbral:
                continue
            # Verificar si está ITM
            if opt.tipo == "CALL" and spot > opt.strike:
                logger.warning("ITM RISK [CALL]: %s strike=%.0f spot=%.0f DTE=%d",
                               opt.simbolo, opt.strike, spot, opt.dias_al_vencimiento)
                en_riesgo.append(pos)
            elif opt.tipo == "PUT" and spot < opt.strike:
                logger.warning("ITM RISK [PUT]: %s strike=%.0f spot=%.0f DTE=%d",
                               opt.simbolo, opt.strike, spot, opt.dias_al_vencimiento)
                en_riesgo.append(pos)
        return en_riesgo

    # ── Callback del feed de mercado ──────────────────────────────────────

    async def on_snapshot(self, snapshot: MarketSnapshot) -> None:
        """
        Hook que el MarketDataFeed llama en cada snapshot.
        Realiza poll de órdenes y verifica riesgo de cierre de mercado.
        """
        await self.poll_orders()
        now_arg = datetime.now(tz=_TZ_ARG).time()

        # Si estamos en la ventana de pre-cierre → cerrar todo
        if _PRECLOSE_CUTOFF <= now_arg < _MARKET_CLOSE:
            await self.close_all_intraday(snapshot)

    # ── Scheduler de cierre automático ───────────────────────────────────

    async def start_auto_close_scheduler(self) -> None:
        """Lanza una tarea que cierra posiciones a las 16:45 Argentina."""
        if self._auto_close_task and not self._auto_close_task.done():
            return
        self._auto_close_task = asyncio.create_task(
            self._auto_close_loop(), name="oms_auto_close"
        )

    async def _auto_close_loop(self) -> None:
        while True:
            now  = datetime.now(tz=_TZ_ARG)
            hoy  = now.date()
            cutoff = datetime(hoy.year, hoy.month, hoy.day,
                              _PRECLOSE_CUTOFF.hour, _PRECLOSE_CUTOFF.minute,
                              tzinfo=_TZ_ARG)
            espera = (cutoff - now).total_seconds()
            if espera > 0:
                logger.info("Auto-close programado en %.0f s (16:45 ARG).", espera)
                await asyncio.sleep(espera)
            await self.close_all_intraday()
            await asyncio.sleep(86_400)   # reprogramar para mañana

    # ── Reporte ───────────────────────────────────────────────────────────

    def reporte(self, snapshot: MarketSnapshot | None = None) -> str:
        """Genera un resumen de posiciones y P&L."""
        lines = [
            f"{'-'*70}",
            f"  OMS {'[DRY-RUN]' if self._dry_run else '[LIVE]'} — "
            f"Perfil {self._profile.name} — {datetime.now():%H:%M:%S}",
            f"{'-'*70}",
        ]

        mid_map: dict[str, float] = {}
        if snapshot:
            for opt in snapshot.opciones:
                if opt.mid:
                    mid_map[opt.simbolo] = opt.mid

        abiertas = self.posiciones_abiertas()
        if not abiertas:
            lines.append("  (sin posiciones abiertas)")
        else:
            lines.append(f"  {'ID':<8} {'Simbolo':<18} {'Lado':<6} {'Cant':>5} "
                         f"{'PrecAp':>9} {'MidMkt':>9} {'P&L no real':>12} {'DTE':>5}")
            lines.append("  " + "-" * 68)
            for pos in abiertas:
                mid   = mid_map.get(pos.simbolo)
                pnl_u = f"{pos.pnl_no_realizado(mid):+.2f}" if mid else "  N/D"
                mid_s = f"{mid:.2f}" if mid else "  N/D"
                lines.append(
                    f"  {pos.id:<8} {pos.simbolo:<18} {pos.lado:<6} {pos.cantidad:>5} "
                    f"{pos.precio_apertura:>9.2f} {mid_s:>9} {pnl_u:>12}"
                )

        cerradas = [p for p in self._positions.values() if p.estado == PositionStatus.CERRADA]
        pnl_total = self.pnl_realizado_total()
        lines += [
            f"{'-'*70}",
            f"  Posiciones cerradas hoy : {len(cerradas)}",
            f"  P&L realizado total     : {pnl_total:+.2f} ARS",
            f"{'-'*70}",
        ]
        return "\n".join(lines)
