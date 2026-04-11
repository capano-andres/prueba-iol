"""
Motor de orquestación multi-estrategia.

Coordina múltiples StrategySlots, cada uno con su propio OMS,
Strategy y MarketDataFeed (compartido por activo).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dotenv import load_dotenv

from iol_client import IOLClient
from market_data import MarketDataFeed, MarketSnapshot
from math_engine import DEFAULT_RISK_FREE_RATE, enrich_snapshot
from oms import OMS, IOLProfile, PositionStatus
from strategy import Strategy, StrategyConfig
from strategy_bull_spread import BullCallSpreadStrategy, BullSpreadConfig
import db

logger = logging.getLogger(__name__)


# ─── Tipos de estrategia disponibles ─────────────────────────────────────────

STRATEGY_TYPES = {
    "options_mispricing": {
        "nombre": "Options Mispricing (BSM)",
        "descripcion": "Provisión de liquidez pasiva. Detecta diferencias entre precio de mercado e IV teórica BSM.",
        "params": [
            {"key": "min_mispricing_pct", "label": "Min Mispricing %", "type": "float", "default": 0.05},
            {"key": "min_spread_pct",     "label": "Min Spread %",     "type": "float", "default": 0.02},
            {"key": "max_spread_pct",     "label": "Max Spread %",     "type": "float", "default": 0.30},
            {"key": "min_dte",            "label": "Min DTE",          "type": "int",   "default": 5},
            {"key": "max_dte",            "label": "Max DTE",          "type": "int",   "default": 45},
            {"key": "min_delta_abs",      "label": "Min |Delta|",      "type": "float", "default": 0.15},
            {"key": "max_delta_abs",      "label": "Max |Delta|",      "type": "float", "default": 0.85},
            {"key": "lote_base",          "label": "Lote Base",        "type": "int",   "default": 1},
            {"key": "max_posiciones_abiertas", "label": "Max Posiciones", "type": "int", "default": 5},
            {"key": "max_drawdown_ars",   "label": "Max Drawdown (Global Stop Loss ARS)", "type": "int", "default": 0},
        ],
    },
    "bull_call_spread": {
        "nombre": "Bull Call Spread Direccional",
        "descripcion": (
            "Spread vertical alcista: compra Call ATM + venta Call OTM del mismo vencimiento. "
            "Mitiga Theta y Vega respecto al Long Call puro. Optimizado para cierre intradiario "
            "(100% bonificación IOL). Basado en análisis cuantitativo COME/GGAL Abril 2026."
        ),
        "params": [
            {"key": "strike_width_pct",     "label": "Ancho Spread %",         "type": "float", "default": 0.12},
            {"key": "atm_offset_pct",       "label": "Offset ATM %",           "type": "float", "default": 0.00},
            {"key": "max_net_premium_pct",  "label": "Max Prima Neta %",       "type": "float", "default": 0.08},
            {"key": "min_dte",              "label": "Min DTE",                "type": "int",   "default": 10},
            {"key": "max_dte",              "label": "Max DTE",                "type": "int",   "default": 75},
            {"key": "min_spread_pct",       "label": "Min Spread Bid-Ask",     "type": "float", "default": 0.01},
            {"key": "max_spread_pct",       "label": "Max Spread Bid-Ask",     "type": "float", "default": 0.25},
            {"key": "lotes_por_spread",     "label": "Lotes x Spread",         "type": "int",   "default": 1},
            {"key": "max_spreads_abiertos", "label": "Max Spreads Simultáneos","type": "int",   "default": 3},
            {"key": "stop_loss_pct",        "label": "Stop Loss (Por Spread) %", "type": "float", "default": 0.80},
            {"key": "take_profit_pct",      "label": "Take Profit (Por Spread) %", "type": "float", "default": 0.65},
            {"key": "min_reward_risk_ratio","label": "Min Reward/Risk",        "type": "float", "default": 0.80},
            {"key": "force_intraday_close", "label": "Cierre Intradiario",     "type": "bool",  "default": False},
            {"key": "max_drawdown_ars",     "label": "Stop Loss Global (Global Max Drawdown ARS)", "type": "int", "default": 0},
        ],
    },
}


# ─── StrategySlot ────────────────────────────────────────────────────────────

@dataclass
class StrategySlot:
    """Instancia de una estrategia en ejecución."""
    id:                str
    nombre:            str
    tipo_estrategia:   str
    activo:            str
    mercado:           str = "bCBA"
    fondos_asignados:  float = 0.0
    config:            dict = field(default_factory=dict)
    dry_run:           bool = True
    estado:            str = "stopped"   # stopped | running | paused
    created_at:        str = ""

    # Instancias de módulos activos (se llenan al arrancar)
    _oms:              OMS | None = field(default=None, repr=False)
    _strategy:         Strategy | None = field(default=None, repr=False)
    _feed_key:         str | None = field(default=None, repr=False)

    # Últimos datos para la UI
    last_snapshot:     MarketSnapshot | None = field(default=None, repr=False)
    last_signals:      list = field(default_factory=list, repr=False)
    logs:              list = field(default_factory=list, repr=False)

    def add_log(self, level: str, message: str) -> None:
        entry = {
            "ts": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        self.logs.append(entry)
        if len(self.logs) > 200:
            self.logs = self.logs[-200:]

    def to_dict(self) -> dict:
        """Serializa el slot para la API."""
        oms = self._oms
        positions_open = []
        pnl_realizado = 0.0
        nominal_en_uso = 0.0

        if oms:
            for p in oms.posiciones_abiertas():
                mid = None
                if self.last_snapshot:
                    for opt in self.last_snapshot.opciones:
                        if opt.simbolo == p.simbolo and opt.mid:
                            mid = opt.mid
                            break
                positions_open.append({
                    "id": p.id,
                    "simbolo": p.simbolo,
                    "tipo": p.tipo,
                    "lado": p.lado,
                    "cantidad": p.cantidad,
                    "precio_apertura": p.precio_apertura,
                    "pnl_no_realizado": p.pnl_no_realizado(mid) if mid else None,
                    "mid_actual": mid,
                })
                nominal_en_uso += p.precio_apertura * p.cantidad
            pnl_realizado = oms.pnl_realizado_total()

        return {
            "id": self.id,
            "nombre": self.nombre,
            "tipo_estrategia": self.tipo_estrategia,
            "activo": self.activo,
            "mercado": self.mercado,
            "fondos_asignados": self.fondos_asignados,
            "nominal_en_uso": nominal_en_uso,
            "config": self.config,
            "dry_run": self.dry_run,
            "estado": self.estado,
            "created_at": self.created_at,
            "pnl_realizado": pnl_realizado,
            "posiciones_abiertas": positions_open,
            "n_posiciones": len(positions_open),
            "last_signals": self.last_signals[-5:],
            "spot": self.last_snapshot.spot if self.last_snapshot else None,
        }


# ─── TradingEngine ───────────────────────────────────────────────────────────

class TradingEngine:
    """
    Motor principal que coordina múltiples StrategySlots.

    Uso:
        engine = TradingEngine()
        await engine.initialize()
        slot_id = await engine.add_strategy({...})
        await engine.start_strategy(slot_id)
    """

    def __init__(self) -> None:
        self._client: IOLClient | None = None
        self._slots: dict[str, StrategySlot] = {}
        self._feeds: dict[str, MarketDataFeed] = {}  # key = "mercado:activo"
        self._ws_callbacks: list = []
        self._initialized = False

    # ── Inicialización ────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Inicializa IOLClient y DB. Carga slots guardados."""
        load_dotenv()
        username = os.getenv("IOL_USERNAME")
        password = os.getenv("IOL_PASSWORD")

        if not username or not password:
            raise RuntimeError("Faltan credenciales IOL en .env")

        # Inicializar DB
        await db.init_db()

        # Conectar a IOL
        self._client = IOLClient(username, password)
        await self._client.__aenter__()
        logger.info("IOLClient conectado.")

        # Cargar slots guardados como stopped
        saved = await db.load_all_slots()
        for s in saved:
            slot = StrategySlot(
                id=s["id"],
                nombre=s["nombre"],
                tipo_estrategia=s["tipo_estrategia"],
                activo=s["activo"],
                mercado=s.get("mercado", "bCBA"),
                fondos_asignados=s.get("fondos_asignados", 0),
                config=s.get("config", {}),
                dry_run=s.get("dry_run", True),
                estado="stopped",
                created_at=s.get("created_at", ""),
            )
            self._slots[slot.id] = slot
            logger.info("Slot cargado: %s (%s)", slot.nombre, slot.id)

        self._initialized = True
        logger.info("TradingEngine inicializado con %d slots.", len(self._slots))

    async def shutdown(self) -> None:
        """Detiene todo y cierra la conexión a IOL."""
        for slot_id in list(self._slots.keys()):
            try:
                await self.stop_strategy(slot_id)
            except Exception as exc:
                logger.error("Error deteniendo slot %s: %s", slot_id, exc)

        for feed in self._feeds.values():
            await feed.stop()
        self._feeds.clear()

        if self._client:
            await self._client.__aexit__(None, None, None)
            self._client = None

        logger.info("TradingEngine apagado.")

    # ── Estado ────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._client is not None and self._initialized

    def get_all_slots(self) -> list[dict]:
        return [s.to_dict() for s in self._slots.values()]

    def get_slot(self, slot_id: str) -> dict | None:
        slot = self._slots.get(slot_id)
        return slot.to_dict() if slot else None

    def get_slot_logs(self, slot_id: str, limit: int = 50) -> list[dict]:
        slot = self._slots.get(slot_id)
        if not slot:
            return []
        return slot.logs[-limit:]

    # ── CRUD de estrategias ──────────────────────────────────────────────

    async def add_strategy(self, data: dict) -> str:
        """Crea un nuevo StrategySlot y lo persiste."""
        slot_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()

        tipo = data.get("tipo_estrategia", "options_mispricing")
        if tipo not in STRATEGY_TYPES:
            raise ValueError(f"Tipo de estrategia desconocido: {tipo}")

        # Usar defaults del tipo de estrategia si no se proporcionan
        defaults = {p["key"]: p["default"] for p in STRATEGY_TYPES[tipo]["params"]}
        config = {**defaults, **data.get("config", {})}

        slot = StrategySlot(
            id=slot_id,
            nombre=data.get("nombre", f"Estrategia {slot_id}"),
            tipo_estrategia=tipo,
            activo=data.get("activo", "GGAL"),
            mercado=data.get("mercado", "bCBA"),
            fondos_asignados=data.get("fondos_asignados", 0),
            config=config,
            dry_run=data.get("dry_run", True),
            estado="stopped",
            created_at=now,
        )

        self._slots[slot_id] = slot

        # Persistir
        await db.save_slot({
            "id": slot.id,
            "nombre": slot.nombre,
            "tipo_estrategia": slot.tipo_estrategia,
            "activo": slot.activo,
            "mercado": slot.mercado,
            "fondos_asignados": slot.fondos_asignados,
            "config": slot.config,
            "dry_run": slot.dry_run,
            "created_at": slot.created_at,
        })

        slot.add_log("INFO", f"Estrategia creada: {slot.nombre}")
        logger.info("Slot creado: %s (%s) — %s en %s",
                     slot.nombre, slot.id, slot.tipo_estrategia, slot.activo)
        await self._broadcast({"type": "slot_created", "slot": slot.to_dict()})
        return slot_id

    async def update_strategy(self, slot_id: str, data: dict) -> dict | None:
        """Actualiza la configuración de un slot (solo si está detenido)."""
        slot = self._slots.get(slot_id)
        if not slot:
            return None

        if slot.estado == "running":
            raise ValueError("No se puede modificar una estrategia en ejecución. Detenela primero.")

        if "nombre" in data:
            slot.nombre = data["nombre"]
        if "fondos_asignados" in data:
            slot.fondos_asignados = data["fondos_asignados"]
        if "config" in data:
            slot.config.update(data["config"])
        if "dry_run" in data:
            slot.dry_run = data["dry_run"]
        if "activo" in data:
            slot.activo = data["activo"]
        if "mercado" in data:
            slot.mercado = data["mercado"]

        # Persistir
        await db.save_slot({
            "id": slot.id,
            "nombre": slot.nombre,
            "tipo_estrategia": slot.tipo_estrategia,
            "activo": slot.activo,
            "mercado": slot.mercado,
            "fondos_asignados": slot.fondos_asignados,
            "config": slot.config,
            "dry_run": slot.dry_run,
            "created_at": slot.created_at,
        })

        slot.add_log("INFO", "Configuración actualizada.")
        await self._broadcast({"type": "slot_updated", "slot": slot.to_dict()})
        return slot.to_dict()

    async def remove_strategy(self, slot_id: str) -> bool:
        """Elimina un slot (deteniéndolo primero si está corriendo)."""
        slot = self._slots.get(slot_id)
        if not slot:
            return False

        if slot.estado == "running":
            await self.stop_strategy(slot_id)

        del self._slots[slot_id]
        await db.delete_slot(slot_id)

        # Limpiar feed si nadie más lo usa
        self._cleanup_feed(slot)

        logger.info("Slot eliminado: %s", slot_id)
        await self._broadcast({"type": "slot_removed", "slot_id": slot_id})
        return True

    # ── Control de ejecución ─────────────────────────────────────────────

    async def start_strategy(self, slot_id: str) -> None:
        """Arranca una estrategia."""
        slot = self._slots.get(slot_id)
        if not slot:
            raise ValueError(f"Slot {slot_id} no encontrado.")
        if slot.estado == "running":
            return

        if not self._client:
            raise RuntimeError("IOLClient no está conectado.")

        feed_key = f"{slot.mercado}:{slot.activo}"

        # Crear o reutilizar feed
        if feed_key not in self._feeds:
            feed = MarketDataFeed(
                self._client,
                mercado=slot.mercado,
                subyacente=slot.activo,
                interval=30.0,
            )
            self._feeds[feed_key] = feed
            await feed.start()
            slot.add_log("INFO", f"Feed de mercado iniciado: {slot.activo}")
        feed = self._feeds[feed_key]

        # Crear OMS para este slot
        profile = IOLProfile.GOLD   # TODO: hacer configurable
        oms = OMS(
            client=self._client,
            mercado=slot.mercado,
            profile=profile,
            dry_run=slot.dry_run,
            max_nominal=slot.fondos_asignados if slot.fondos_asignados > 0 else None,
        )
        slot._oms = oms

        # Crear Strategy según el tipo configurado
        if slot.tipo_estrategia == "bull_call_spread":
            bcs_cfg = BullSpreadConfig(**{
                k: v for k, v in slot.config.items()
                if k in BullSpreadConfig.__dataclass_fields__
            })
            strategy = BullCallSpreadStrategy(oms, bcs_cfg)
        else:
            # default: options_mispricing
            cfg = StrategyConfig(**{
                k: v for k, v in slot.config.items()
                if k in StrategyConfig.__dataclass_fields__
            })
            strategy = Strategy(oms, cfg)
        slot._strategy = strategy
        slot._feed_key = feed_key

        # Conectar callbacks al feed
        async def _on_snapshot(snapshot: MarketSnapshot, _slot=slot) -> None:
            if _slot.estado != "running":
                return
            _slot.last_snapshot = snapshot

            # OMS hook (poll órdenes + cierre automático 16:45)
            await _slot._oms.on_snapshot(snapshot)

            # ── Max Drawdown (Global Stop Loss) ──
            max_drawdown = _slot.config.get("max_drawdown_ars", 0)
            if max_drawdown > 0:
                slot_info = _slot.to_dict()
                unrealized = sum(p.get("pnl_no_realizado") or 0 for p in slot_info.get("posiciones_abiertas", []))
                realized = slot_info.get("pnl_realizado", 0)
                total_pnl = realized + unrealized
                
                # Si estamos perdiendo más del max_drawdown configurado
                if total_pnl <= -max_drawdown:
                    _slot.add_log("CRITICAL", f"⚠️ GLOBAL STOP LOSS ALCANZADO: P&L {total_pnl:.2f} ARS <= -{max_drawdown}. FORZANDO CIERRE Y APAGADO...")
                    # Ejecutar apagado asíncrono y salir de este snapshot
                    asyncio.create_task(self.stop_strategy(_slot.id))
                    return

            # ── Dispatch por tipo de estrategia ──────────────────────────
            if _slot.tipo_estrategia == "bull_call_spread":
                bcs: BullCallSpreadStrategy = _slot._strategy  # type: ignore[assignment]

                # Forzar cierre intradiario si el OMS ya lo disparó (pre-close)
                # La estrategia BCS tiene su propio método cerrar_todos
                from datetime import datetime, time, timezone, timedelta
                _TZ_ARG = timezone(timedelta(hours=-3))
                now_arg = datetime.now(tz=_TZ_ARG).time()
                if bcs._config.force_intraday_close and time(16, 45) <= now_arg < time(17, 0):
                    await bcs.cerrar_todos(snapshot)
                else:
                    await bcs.on_snapshot(snapshot)

                # Serializar estado de spreads abiertos como "signals" para la UI
                spreads = bcs.resumen_spreads()
                _slot.last_signals = [
                    {
                        "simbolo": sp["long_simbolo"] + "/" + sp["short_simbolo"],
                        "lado": "SPREAD",
                        "precio": sp["net_premium"],
                        "razon": (
                            f"K={sp['long_strike']:.0f}/{sp['short_strike']:.0f} "
                            f"BEP={sp['breakeven']:.1f} R/R={sp['reward_risk']:.2f} "
                            f"MaxP={sp['max_profit']:.1f} DTE={sp['dte']}"
                        ),
                        "score": sp["reward_risk"],
                    }
                    for sp in spreads[:10]
                ]
                if spreads:
                    abiertos = [sp for sp in spreads if sp["is_open"]]
                    if abiertos:
                        top = abiertos[0]
                        _slot.add_log("INFO",
                            f"{len(abiertos)} spreads abiertos. Top: "
                            f"K={top['long_strike']:.0f}/{top['short_strike']:.0f} "
                            f"R/R={top['reward_risk']:.2f}"
                        )

            else:
                # options_mispricing (y cualquier futuro tipo BSM)
                pricings = enrich_snapshot(snapshot, r=DEFAULT_RISK_FREE_RATE)
                if pricings:
                    signals = _slot._strategy.evaluar(pricings)
                    _slot.last_signals = [
                        {
                            "simbolo": s.pricing.quote.simbolo,
                            "lado": s.lado,
                            "precio": s.precio_limite,
                            "razon": s.razon,
                            "score": round(s.score, 4),
                        }
                        for s in signals[:10]
                    ]
                    if signals:
                        _slot.add_log("INFO",
                            f"{len(signals)} señales encontradas. Top: {signals[0].pricing.quote.simbolo} "
                            f"{signals[0].lado} score={signals[0].score:.3f}"
                        )
                        await _slot._strategy.ejecutar_signals(signals)

            # Broadcast a WebSocket
            await self._broadcast({
                "type": "snapshot",
                "slot_id": _slot.id,
                "data": _slot.to_dict(),
            })

        feed.on_snapshot(_on_snapshot)

        slot.estado = "running"
        mode = "DRY-RUN" if slot.dry_run else "LIVE"
        slot.add_log("INFO", f"Estrategia arrancada [{mode}]")
        logger.info("Slot %s arrancado [%s]: %s en %s",
                     slot.id, mode, slot.nombre, slot.activo)
        await self._broadcast({"type": "slot_started", "slot": slot.to_dict()})

    async def pause_strategy(self, slot_id: str) -> None:
        """Pausa una estrategia (deja de evaluar, pero el feed sigue)."""
        slot = self._slots.get(slot_id)
        if not slot or slot.estado != "running":
            return
        slot.estado = "paused"
        slot.add_log("INFO", "Estrategia pausada.")
        await self._broadcast({"type": "slot_paused", "slot": slot.to_dict()})

    async def stop_strategy(self, slot_id: str) -> None:
        """Detiene una estrategia y su OMS."""
        slot = self._slots.get(slot_id)
        if not slot or slot.estado == "stopped":
            return

        # Cerrar posiciones abiertas antes de detener
        if slot._oms:
            abiertas = slot._oms.posiciones_abiertas()
            if abiertas:
                slot.add_log("WARNING",
                    f"Cerrando {len(abiertas)} posiciones abiertas antes de detener..."
                )
                await slot._oms.close_all_intraday(slot.last_snapshot)

        slot.estado = "stopped"
        slot._oms = None
        slot._strategy = None

        # Limpiar feed si nadie más lo usa
        self._cleanup_feed(slot)

        slot.add_log("INFO", "Estrategia detenida.")
        logger.info("Slot %s detenido: %s", slot.id, slot.nombre)
        await self._broadcast({"type": "slot_stopped", "slot": slot.to_dict()})

    # ── Cuenta IOL ───────────────────────────────────────────────────────

    async def get_account_info(self) -> dict:
        """Obtiene información de la cuenta IOL."""
        if not self._client:
            return {"error": "No conectado"}
        try:
            account = await self._client.get_account_state()
            return account
        except Exception as exc:
            logger.error("Error obteniendo cuenta: %s", exc)
            return {"error": str(exc)}

    # ── WebSocket ────────────────────────────────────────────────────────

    def register_ws(self, callback) -> None:
        self._ws_callbacks.append(callback)

    def unregister_ws(self, callback) -> None:
        if callback in self._ws_callbacks:
            self._ws_callbacks.remove(callback)

    async def _broadcast(self, data: dict) -> None:
        """Envía datos a todos los WebSocket conectados."""
        dead = []
        for cb in self._ws_callbacks:
            try:
                await cb(data)
            except Exception:
                dead.append(cb)
        for cb in dead:
            self._ws_callbacks.remove(cb)

    # ── Helpers privados ─────────────────────────────────────────────────

    def _cleanup_feed(self, slot: StrategySlot) -> None:
        """Detiene un feed si ningún slot lo necesita."""
        if not slot._feed_key:
            return
        key = slot._feed_key
        # Verificar si algún otro slot activo usa este feed
        for s in self._slots.values():
            if s.id != slot.id and s._feed_key == key and s.estado in ("running", "paused"):
                return
        # Nadie lo usa → lo dejamos por ahora (podría detenerse para ahorrar requests)
        # feed = self._feeds.pop(key, None)
        # if feed:
        #     asyncio.create_task(feed.stop())
