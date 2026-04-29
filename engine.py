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
from datetime import datetime, timezone, timedelta
from typing import Any

_TZ_ARG = timezone(timedelta(hours=-3))

from dotenv import load_dotenv

from iol_client import IOLClient
from market_data import MarketDataFeed, MarketSnapshot, StockDataFeed, StockSnapshot
from math_engine import DEFAULT_RISK_FREE_RATE, enrich_snapshot, bsm_greeks
from oms import OMS, IOLProfile, PositionStatus
from strategy import Strategy, StrategyConfig
from strategy_bull_spread import BullCallSpreadStrategy, BullSpreadConfig
from strategy_long_directional import LongDirectionalStrategy, LongDirectionalConfig
from strategy_daytrading import DaytradingStrategy, DaytradingConfig
from strategy_rsi_options import RsiOptionsStrategy, RsiOptionsConfig
from strategy_acciones_ema import AccionesEMAStrategy, AccionesEMAConfig
import db

logger = logging.getLogger(__name__)


# ─── Tipos de estrategia disponibles ─────────────────────────────────────────

STRATEGY_TYPES = {
    "options_mispricing": {
        "nombre": "Options Mispricing (BSM)",
        "descripcion": "Provisión de liquidez pasiva. Detecta diferencias entre precio de mercado e IV teórica BSM.",
        "params": [
            {"key": "min_mispricing_pct", "label": "Min Mispricing %", "type": "float", "default": 0.05, "descripcion": "Desviación mínima vs precio teórico BSM para operar. Ejemplo: 0.05 = 5%, 0.10 = 10%."},
            {"key": "min_spread_pct",     "label": "Min Spread %",     "type": "float", "default": 0.02, "descripcion": "Spread compra-venta mínimo requerido. Ejemplo: 0.02 = 2%, 0.05 = 5%."},
            {"key": "max_spread_pct",     "label": "Max Spread %",     "type": "float", "default": 0.30, "descripcion": "Spread máximo tolerable (opciones muy ilíquidas). Ejemplo: 0.30 = 30%."},
            {"key": "min_dte",            "label": "Min DTE",          "type": "int",   "default": 1, "descripcion": "Días mínimos al vencimiento. 1 = sin restricción."},
            {"key": "max_dte",            "label": "Max DTE",          "type": "int",   "default": 45, "descripcion": "Días máximos al vencimiento."},
            {"key": "min_delta_abs",      "label": "Min |Delta|",      "type": "float", "default": 0.15, "descripcion": "Delta mínimo absoluto. Filtra opciones muy OTM que no se mueven. Ejemplo: 0.15 = delta 15."},
            {"key": "max_delta_abs",      "label": "Max |Delta|",      "type": "float", "default": 0.85, "descripcion": "Delta máximo absoluto. Evita opciones Deep ITM muy caras. Ejemplo: 0.85 = delta 85."},
            {"key": "lote_base",          "label": "Lote Base",        "type": "int",   "default": 1, "descripcion": "Cantidad de contratos a comprar por orden."},
            {"key": "max_posiciones_abiertas", "label": "Max Posiciones", "type": "int", "default": 5, "descripcion": "Contratos concurrentes máximos abiertos."},
            {"key": "max_drawdown_ars",   "label": "Max Drawdown (ARS)", "type": "int", "default": 0, "descripcion": "Pérdida máxima en pesos antes de auto-liquidar todo. 0 = sin límite."},
        ],
    },
    "bull_call_spread": {
        "nombre": "Bull Call Spread Direccional",
        "descripcion": (
            "Spread vertical alcista: compra Call ATM + venta Call OTM del mismo vencimiento. "
            "Mitiga Theta y Vega respecto al Long Call puro. Optimizado para cierre intradiario."
        ),
        "params": [
            {"key": "strike_width_pct",     "label": "Ancho Spread %",         "type": "float", "default": 0.12, "descripcion": "Distancia entre strikes. Ejemplo: 0.12 = 12% entre el comprado y el vendido."},
            {"key": "atm_offset_pct",       "label": "Offset ATM %",           "type": "float", "default": 0.00, "descripcion": "Desplazamiento del punto de partida. 0 = comprar justo At The Money."},
            {"key": "max_net_premium_pct",  "label": "Max Prima Neta %",       "type": "float", "default": 0.08, "descripcion": "Costo máximo del spread como % del spot. Ejemplo: 0.08 = 8% del precio."},
            {"key": "min_dte",              "label": "Min DTE",                "type": "int",   "default": 1, "descripcion": "Días mínimos al vencimiento."},
            {"key": "max_dte",              "label": "Max DTE",                "type": "int",   "default": 75, "descripcion": "Días máximos al vencimiento."},
            {"key": "min_spread_pct",       "label": "Min Spread Bid-Ask",     "type": "float", "default": 0.01, "descripcion": "Spread bid-ask mínimo. Ejemplo: 0.01 = 1%. Filtra cotizaciones estancadas."},
            {"key": "max_spread_pct",       "label": "Max Spread Bid-Ask",     "type": "float", "default": 0.25, "descripcion": "Spread bid-ask máximo tolerable. Ejemplo: 0.25 = 25%."},
            {"key": "lotes_por_spread",     "label": "Lotes x Spread",         "type": "int",   "default": 1, "descripcion": "Pares de contratos por operación."},
            {"key": "max_spreads_abiertos", "label": "Max Spreads Simultáneos","type": "int",   "default": 3, "descripcion": "Máximo de spreads abiertos al mismo tiempo."},
            {"key": "stop_loss_pct",        "label": "Stop Loss %",            "type": "float", "default": 0.80, "descripcion": "Cerrar si la prima neta cae este %. Ejemplo: 0.80 = -80% de lo pagado."},
            {"key": "take_profit_pct",      "label": "Take Profit %",          "type": "float", "default": 0.65, "descripcion": "Cerrar si la ganancia alcanza este %. Ejemplo: 0.65 = +65% de ganancia."},
            {"key": "min_reward_risk_ratio","label": "Min Reward/Risk",        "type": "float", "default": 0.80, "descripcion": "Ratio mínimo ganancia/riesgo. Ejemplo: 0.80 = por cada $1 que arriesgás, ganás al menos $0.80."},
            {"key": "force_intraday_close", "label": "Cierre Intradiario",     "type": "bool",  "default": False, "descripcion": "Cerrar todo a las 16:45 para bonificación IOL."},
            {"key": "max_drawdown_ars",     "label": "Max Drawdown (ARS)",     "type": "int",   "default": 0, "descripcion": "Pérdida máxima en pesos antes de auto-liquidar. 0 = sin límite."},
        ],
    },
    "long_directional": {
        "nombre": "Direccional Puro (Solo Compras)",
        "descripcion": (
            "Compra opciones a favor de tu tendencia sin lanzar al descubierto. "
            "Ideal para buscar explosiones especulativas con Stop Loss."
        ),
        "params": [
            {"key": "sesgo",              "label": "Sesgo",              "type": "string",  "default": "CALL", "descripcion": "CALL si creés que sube, PUT si creés que baja."},
            {"key": "target_delta_abs",   "label": "Target Delta |Δ|",   "type": "float",   "default": 0.50, "descripcion": "Delta objetivo. 0.50 = ATM (At The Money). Menor = más barato pero más especulativo."},
            {"key": "max_premium_pct",    "label": "Max Prima %",        "type": "float",   "default": 0.10, "descripcion": "Costo máximo permitido como % del spot. Ejemplo: 0.10 = 10% del precio de la acción."},
            {"key": "stop_loss_pct",      "label": "Stop Loss %",        "type": "float",   "default": 0.35, "descripcion": "Vender si la prima cae este %. Ejemplo: 0.35 = vender si pierde 35% de lo pagado."},
            {"key": "take_profit_pct",    "label": "Take Profit %",      "type": "float",   "default": 0.60, "descripcion": "Vender si la prima sube este %. Ejemplo: 0.60 = tomar ganancia al +60%."},
            {"key": "min_dte",            "label": "Min DTE",            "type": "int",     "default": 1, "descripcion": "Días mínimos al vencimiento."},
            {"key": "max_dte",            "label": "Max DTE",            "type": "int",     "default": 90, "descripcion": "Días máximos al vencimiento."},
            {"key": "max_spread_pct",     "label": "Max Spread Bid-Ask", "type": "float",   "default": 0.25, "descripcion": "Spread bid-ask máximo tolerable. Ejemplo: 0.25 = 25%."},
            {"key": "lotes_por_trade",    "label": "Lotes por Trade",    "type": "int",     "default": 1, "descripcion": "Contratos a comprar por señal."},
            {"key": "max_posiciones_abiertas", "label": "Max Posiciones", "type": "int", "default": 2, "descripcion": "Máximo de posiciones abiertas simultáneas."},
            {"key": "force_intraday_close", "label": "Cierre Intradiario", "type": "bool", "default": False, "descripcion": "Cerrar todo a las 16:45 para bonificación IOL."},
            {"key": "max_drawdown_ars",   "label": "Max Drawdown (ARS)", "type": "int", "default": 0, "descripcion": "Pérdida máxima en pesos antes de auto-liquidar. 0 = sin límite."},
        ],
    },
    "daytrading_acciones": {
        "nombre": "Daytrading Opciones (EMA + RSI)",
        "descripcion": (
            "Intradiario bidireccional sobre primas de opciones GGAL. "
            "Compra CALLs en tendencia alcista (EMA golden cross) y "
            "PUTs en tendencia bajista (EMA death cross). Solo compra opciones, "
            "nunca lanza al descubierto. Tradea el valor de la prima."
        ),
        "params": [
            {"key": "ema_rapida",         "label": "EMA Rápida",            "type": "int",   "default": 9,    "descripcion": "Períodos de la Media Móvil Exponencial rápida. Cada período = 1 snapshot (~30s)."},
            {"key": "ema_lenta",          "label": "EMA Lenta",             "type": "int",   "default": 21,   "descripcion": "Períodos EMA lenta. Cuando la rápida la cruza hacia arriba = señal alcista, hacia abajo = bajista."},
            {"key": "rsi_periodos",       "label": "RSI Períodos",          "type": "int",   "default": 14,   "descripcion": "Períodos para calcular el Índice de Fuerza Relativa (RSI)."},
            {"key": "rsi_overbought",     "label": "RSI Sobrecompra",       "type": "int",   "default": 70,   "descripcion": "No comprar CALLs si RSI supera este nivel (mercado recalentado)."},
            {"key": "rsi_oversold",       "label": "RSI Sobreventa",        "type": "int",   "default": 30,   "descripcion": "No comprar PUTs si RSI cae debajo de este nivel (mercado ya colapsado)."},
            {"key": "target_delta",       "label": "Delta Objetivo",        "type": "float", "default": 0.50, "descripcion": "Delta absoluto para seleccionar opción. 0.50 = ATM (At The Money)."},
            {"key": "min_dte",            "label": "Min DTE",               "type": "int",   "default": 1,    "descripcion": "Días mínimos al vencimiento."},
            {"key": "max_dte",            "label": "Max DTE",               "type": "int",   "default": 45,   "descripcion": "Días máximos al vencimiento."},
            {"key": "max_spread_pct",     "label": "Max Spread Bid-Ask",    "type": "float", "default": 0.25, "descripcion": "Spread bid-ask máximo tolerable. Ejemplo: 0.25 = 25%."},
            {"key": "stop_loss_pct",      "label": "Stop Loss %",           "type": "float", "default": 0.20, "descripcion": "Vender si la prima cae este %. Ejemplo: 0.20 = vender si pierde -20% de lo pagado."},
            {"key": "take_profit_pct",    "label": "Take Profit %",         "type": "float", "default": 0.40, "descripcion": "Vender si la prima sube este %. Ejemplo: 0.40 = tomar ganancia al +40%."},
            {"key": "lotes_por_trade",    "label": "Lotes por Trade",       "type": "int",   "default": 1,    "descripcion": "Contratos a comprar por señal."},
            {"key": "max_posiciones",     "label": "Max Posiciones",        "type": "int",   "default": 2,    "descripcion": "Máximo de posiciones abiertas simultáneas."},
            {"key": "cooldown_snapshots", "label": "Cooldown (snapshots)",  "type": "int",   "default": 5,    "descripcion": "Snapshots de espera post-cierre antes de abrir otra posición (~2.5 min)."},
            {"key": "force_intraday_close", "label": "Cierre Intradiario", "type": "bool",  "default": True,  "descripcion": "Forzar cierre total a las 16:45 para bonificación IOL."},
            {"key": "max_drawdown_ars",   "label": "Stop Loss Global (ARS)", "type": "int", "default": 0,    "descripcion": "Corta todo si la pérdida acumulada supera este monto."},
        ],
    },
    "rsi_options": {
        "nombre": "RSI Options (Solo RSI)",
        "descripcion": (
            "Compra PUTs cuando el RSI supera el umbral de sobrecompra y "
            "CALLs cuando cae bajo el umbral de sobreventa. "
            "Sin EMA — señales más frecuentes que Daytrading. Solo compra opciones, nunca lanza."
        ),
        "params": [
            {"key": "rsi_periodos",       "label": "RSI Períodos",          "type": "int",   "default": 14,   "descripcion": "Períodos para calcular el RSI. Cada período = 1 vela cerrada."},
            {"key": "candle_minutes",     "label": "Vela (minutos)",        "type": "int",   "default": 5,    "descripcion": "Tamaño de vela OHLC. 5m es estándar para daytrading. Warmup ≈ (RSI Períodos + 1) × este valor."},
            {"key": "rsi_overbought",     "label": "RSI Sobrecompra",       "type": "float", "default": 70.0, "descripcion": "RSI por encima de este nivel → comprar PUT (mercado recalentado)."},
            {"key": "rsi_oversold",       "label": "RSI Sobreventa",        "type": "float", "default": 30.0, "descripcion": "RSI por debajo de este nivel → comprar CALL (mercado colapsado)."},
            {"key": "target_delta",       "label": "Delta Objetivo",        "type": "float", "default": 0.50, "descripcion": "Delta absoluto para seleccionar opción. 0.50 = ATM (At The Money)."},
            {"key": "min_dte",            "label": "Min DTE",               "type": "int",   "default": 5,    "descripcion": "Días mínimos al vencimiento."},
            {"key": "max_dte",            "label": "Max DTE",               "type": "int",   "default": 45,   "descripcion": "Días máximos al vencimiento."},
            {"key": "max_spread_pct",     "label": "Max Spread Bid-Ask",    "type": "float", "default": 0.40, "descripcion": "Spread bid-ask máximo tolerable. Opciones GGAL suelen tener spreads del 30–40%."},
            {"key": "stop_loss_pct",      "label": "Stop Loss %",           "type": "float", "default": 0.20, "descripcion": "Vender si la prima cae este %. Ejemplo: 0.20 = vender si pierde -20%."},
            {"key": "take_profit_pct",    "label": "Take Profit %",         "type": "float", "default": 0.40, "descripcion": "Vender si la prima sube este %. Ejemplo: 0.40 = tomar ganancia al +40%."},
            {"key": "lotes_por_trade",    "label": "Lotes por Trade",       "type": "int",   "default": 1,    "descripcion": "Contratos a comprar por señal."},
            {"key": "max_posiciones",     "label": "Max Posiciones",        "type": "int",   "default": 2,    "descripcion": "Máximo de posiciones abiertas simultáneas."},
            {"key": "cooldown_snapshots", "label": "Cooldown (snapshots)",  "type": "int",   "default": 5,    "descripcion": "Snapshots de espera post-entrada antes de volver a operar (~2.5 min)."},
            {"key": "force_intraday_close", "label": "Cierre Intradiario", "type": "bool",  "default": True,  "descripcion": "Forzar cierre total a las 16:45 para bonificación IOL."},
            {"key": "max_drawdown_ars",   "label": "Stop Loss Global (ARS)", "type": "int", "default": 0,    "descripcion": "Corta todo si la pérdida acumulada supera este monto. 0 = sin límite."},
        ],
    },
    "acciones_ema": {
        "nombre": "Acciones EMA + RSI",
        "descripcion": (
            "Opera directamente sobre acciones argentinas (bCBA) usando cruce de EMAs "
            "y RSI como filtro. Compra en Golden Cross, vende en Death Cross o al activarse SL/TP. "
            "Solo posiciones LONG — sin venta en descubierto."
        ),
        "params": [
            {"key": "ema_rapida",         "label": "EMA Rápida",            "type": "int",    "default": 9,          "descripcion": "Períodos EMA rápida. Cada período = 1 snapshot (~30s)."},
            {"key": "ema_lenta",          "label": "EMA Lenta",             "type": "int",    "default": 21,         "descripcion": "Períodos EMA lenta. 21 snapshots ≈ 10.5 min de warmup hasta operar."},
            {"key": "rsi_periodos",       "label": "RSI Períodos",          "type": "int",    "default": 14,         "descripcion": "Períodos para el RSI."},
            {"key": "rsi_overbought",     "label": "RSI Sobrecompra",       "type": "int",    "default": 70,         "descripcion": "No comprar si RSI supera este nivel."},
            {"key": "rsi_oversold",       "label": "RSI Sobreventa",        "type": "int",    "default": 30,         "descripcion": "No comprar si RSI cae por debajo de este nivel."},
            {"key": "stop_loss_pct",      "label": "Stop Loss %",           "type": "float",  "default": 0.05,       "descripcion": "Vender si el precio cae este %. Ej: 0.05 = -5%."},
            {"key": "take_profit_pct",    "label": "Take Profit %",         "type": "float",  "default": 0.10,       "descripcion": "Vender si el precio sube este %. Ej: 0.10 = +10%."},
            {"key": "modo_cantidad",      "label": "Modo Dimensionamiento", "type": "string", "default": "cantidad", "descripcion": "'cantidad' = N acciones fijas por trade. 'monto' = monto ARS fijo (calcula automáticamente cuántas acciones comprar al precio actual)."},
            {"key": "cantidad_acciones",  "label": "Cantidad Acciones",     "type": "int",    "default": 1,          "descripcion": "Acciones a comprar por trade (solo si modo=cantidad)."},
            {"key": "monto_por_trade",    "label": "Monto por Trade (ARS)", "type": "float",  "default": 50000,      "descripcion": "ARS a invertir por trade (solo si modo=monto). Calcula la cantidad al precio actual."},
            {"key": "max_posiciones",     "label": "Max Posiciones",        "type": "int",    "default": 1,          "descripcion": "Máximo de posiciones abiertas simultáneas."},
            {"key": "cooldown_snapshots", "label": "Cooldown (snapshots)",  "type": "int",    "default": 3,          "descripcion": "Snapshots de espera post-cierre antes de abrir otra posición."},
            {"key": "force_close_eod",    "label": "Cierre EOD 16:45",      "type": "bool",   "default": True,       "descripcion": "Cerrar todas las posiciones a las 16:45 para evitar quedar posicionado overnight."},
            {"key": "max_drawdown_ars",   "label": "Stop Loss Global (ARS)","type": "int",    "default": 0,          "descripcion": "Corta todo si la pérdida acumulada supera este monto. 0 = sin límite."},
        ],
    },
}

# Inyectar auto_restart en todos los tipos (parámetro global de infraestructura)
_AUTO_RESTART_PARAM = {
    "key": "auto_restart",
    "label": "Auto-Restart al iniciar servidor",
    "type": "bool",
    "default": False,
    "descripcion": (
        "Si está activado, la estrategia se arrancará automáticamente cada vez que "
        "se inicie el servidor. Útil para operaciones que deben correr todos los días sin intervención manual."
    ),
}
for _t in STRATEGY_TYPES.values():
    if not any(p["key"] == "auto_restart" for p in _t["params"]):
        _t["params"].append(_AUTO_RESTART_PARAM)


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
    win_stats:         dict = field(default_factory=lambda: {"total": 0, "ganadas": 0, "perdidas": 0, "win_rate": 0.0}, repr=False)

    def add_log(self, level: str, message: str) -> None:
        entry = {
            "ts": datetime.now(tz=_TZ_ARG).strftime("%H:%M:%S"),
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
        pnl_no_realizado_total = 0.0
        nominal_en_uso = 0.0
        net_delta = 0.0
        net_vega = 0.0

        # Determinar spot según tipo de snapshot
        snap = self.last_snapshot
        if snap is None:
            spot = None
        elif hasattr(snap, "opciones"):
            # MarketSnapshot (opciones)
            spot = snap.spot
        else:
            # StockSnapshot
            spot = getattr(snap, "precio", None)

        if oms:
            for p in oms.posiciones_abiertas():
                mid = None
                opt_quote = None

                if snap is not None and hasattr(snap, "opciones"):
                    # Solo buscar mid en cadena de opciones para estrategias de opciones
                    for opt in snap.opciones:
                        if opt.simbolo == p.simbolo:
                            mid = opt.mid
                            opt_quote = opt
                            break
                elif snap is not None and not hasattr(snap, "opciones"):
                    # Para acciones: el mid es el precio de la acción
                    mid = spot

                pnl_nr = p.pnl_no_realizado(mid) if mid else None
                if pnl_nr is not None:
                    pnl_no_realizado_total += pnl_nr

                # Calcular Griegas Activas para esta posición (solo opciones)
                pos_delta = None
                pos_vega = None
                if opt_quote and spot and spot > 0:
                    try:
                        T = opt_quote.dias_al_vencimiento / 365.0
                        if T > 0 and opt_quote.strike > 0:
                            sigma = 0.80  # σ de referencia para griegas de UI
                            greeks = bsm_greeks(
                                opt_quote.tipo, spot, opt_quote.strike,
                                T, DEFAULT_RISK_FREE_RATE, sigma
                            )
                            if greeks and greeks.delta is not None and greeks.vega is not None:
                                mult = 100 * p.cantidad
                                sign = 1 if p.lado == "LONG" else -1
                                pos_delta = greeks.delta * mult * sign
                                pos_vega = greeks.vega * mult * sign
                                net_delta += pos_delta
                                net_vega += pos_vega
                    except Exception:
                        pass

                positions_open.append({
                    "id": p.id,
                    "simbolo": p.simbolo,
                    "tipo": p.tipo,
                    "lado": p.lado,
                    "cantidad": p.cantidad,
                    "precio_apertura": p.precio_apertura,
                    "pnl_no_realizado": pnl_nr,
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
            "pnl_no_realizado_total": pnl_no_realizado_total,
            "net_delta": net_delta,
            "net_vega": net_vega,
            "win_stats": self.win_stats,
            "posiciones_abiertas": positions_open,
            "n_posiciones": len(positions_open),
            "last_signals": self.last_signals[-5:],
            "spot": spot,
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
        slots_to_autostart = []
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
            slot.win_stats = await db.get_win_rate_stats(slot.id)
            self._slots[slot.id] = slot
            logger.info("Slot cargado: %s (%s)", slot.nombre, slot.id)

            # Marcar para auto-restart si estaba corriendo y tiene el flag activo
            if slot.config.get("auto_restart", False):
                slots_to_autostart.append(slot.id)

        self._initialized = True
        logger.info("TradingEngine inicializado con %d slots.", len(self._slots))

        # Auto-restart de estrategias configuradas para ello
        for slot_id in slots_to_autostart:
            try:
                s = self._slots[slot_id]
                logger.info(
                    "Auto-restart activado: arrancando '%s' (%s)...",
                    s.nombre, slot_id,
                )
                await self.start_strategy(slot_id)
            except Exception as exc:
                logger.error(
                    "Error en auto-restart de slot %s: %s", slot_id, exc
                )


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

    async def reconnect(self) -> bool:
        """Fuerza re-autenticación del IOLClient."""
        if not self._client:
            logger.error("reconnect: No hay IOLClient instanciado.")
            return False
        try:
            logger.info("Forzando re-autenticación IOL...")
            await self._client.authenticate()
            logger.info("Re-autenticación exitosa.")
            return True
        except Exception as exc:
            logger.error("Error en re-autenticación: %s", exc)
            return False

    # ── Estado ────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._client is not None and self._initialized

    def get_all_slots(self) -> list[dict]:
        return [s.to_dict() for s in self._slots.values()]

    def get_slot(self, slot_id: str) -> dict | None:
        slot = self._slots.get(slot_id)
        return slot.to_dict() if slot else None

    async def get_account_info(self) -> dict:
        """Fetch real-time account state from IOL."""
        if not self._client:
            return {"error": "No conectado a IOL"}
        try:
            return await self._client.get_account_state()
        except Exception as exc:
            logger.error(f"Error fetching account info: {exc}")
            return {"error": str(exc)}
            
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

        slot.win_stats = await db.get_win_rate_stats(slot.id)

        if not self._client:
            raise RuntimeError("IOLClient no está conectado.")

        feed_key = f"{slot.mercado}:{slot.activo}"

        # ── Para acciones_ema usamos StockDataFeed (liviano, solo get_quote)
        if slot.tipo_estrategia == "acciones_ema":
            stock_feed_key = f"stock:{slot.mercado}:{slot.activo}"
            if stock_feed_key not in self._feeds:
                stock_feed = StockDataFeed(
                    self._client,
                    simbolo=slot.activo,
                    mercado=slot.mercado,
                    interval=30.0,
                )
                self._feeds[stock_feed_key] = stock_feed
                await stock_feed.start()
                slot.add_log("INFO", f"Feed de acciones iniciado: {slot.activo}")
            feed = self._feeds[stock_feed_key]
            slot._feed_key = stock_feed_key
        else:
            # Crear o reutilizar feed de opciones
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
            slot._feed_key = feed_key

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
        elif slot.tipo_estrategia == "long_directional":
            ld_cfg = LongDirectionalConfig(**{
                k: v for k, v in slot.config.items()
                if k in LongDirectionalConfig.__dataclass_fields__
            })
            strategy = LongDirectionalStrategy(oms, ld_cfg)
        elif slot.tipo_estrategia == "daytrading_acciones":
            dt_cfg = DaytradingConfig(**{
                k: v for k, v in slot.config.items()
                if k in DaytradingConfig.__dataclass_fields__
            })
            strategy = DaytradingStrategy(oms, dt_cfg)
        elif slot.tipo_estrategia == "rsi_options":
            ro_cfg = RsiOptionsConfig(**{
                k: v for k, v in slot.config.items()
                if k in RsiOptionsConfig.__dataclass_fields__
            })
            strategy = RsiOptionsStrategy(oms, ro_cfg)
        elif slot.tipo_estrategia == "acciones_ema":
            ae_cfg = AccionesEMAConfig(**{
                k: v for k, v in slot.config.items()
                if k in AccionesEMAConfig.__dataclass_fields__
            })
            strategy = AccionesEMAStrategy(oms, ae_cfg)
        else:
            # default: options_mispricing
            cfg = StrategyConfig(**{
                k: v for k, v in slot.config.items()
                if k in StrategyConfig.__dataclass_fields__
            })
            strategy = Strategy(oms, cfg)
        slot._strategy = strategy
        slot._feed_key = feed_key

        # Conectar callbacks al feed (limpiar callback previo si existe)
        # Guardar referencia para poder removerlo después
        if hasattr(slot, '_snapshot_callback') and slot._snapshot_callback:
            feed.remove_callback(slot._snapshot_callback)

        async def _on_snapshot(snapshot: MarketSnapshot, _slot=slot) -> None:
            if _slot.estado != "running":
                return
            _slot.last_snapshot = snapshot

            # OMS hook (poll órdenes + cierre automático 16:45)
            await _slot._oms.on_snapshot(snapshot)

            # ── Fuera de horario de mercado: no analizar ──────────────────
            from datetime import datetime, time as _t, timezone, timedelta
            _now_arg = datetime.now(tz=timezone(timedelta(hours=-3))).time()
            if not (_t(10, 30) <= _now_arg < _t(17, 0)):
                return

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

            elif _slot.tipo_estrategia == "long_directional":
                ld: LongDirectionalStrategy = _slot._strategy  # type: ignore[assignment]

                # Forzar cierre intradiario si aplica
                from datetime import datetime, time, timezone, timedelta
                _TZ_ARG = timezone(timedelta(hours=-3))
                now_arg = datetime.now(tz=_TZ_ARG).time()
                if ld._config.force_intraday_close and time(16, 45) <= now_arg < time(17, 0):
                    await ld.cerrar_todos(snapshot)
                else:
                    await ld.on_snapshot(snapshot)

                # Serializar señales para la UI
                _slot.last_signals = [
                    {
                        "simbolo": s.opcion.quote.simbolo,
                        "lado": "LONG",
                        "precio": s.costo,
                        "razon": s.informacion,
                        "score": round(s.score, 4),
                    }
                    for s in ld._last_evaluated_signals[:10]
                ]
                if ld._last_evaluated_signals:
                    top = ld._last_evaluated_signals[0]
                    _slot.add_log("INFO", f"Señal: {top.informacion}")

            elif _slot.tipo_estrategia == "daytrading_acciones":
                dt: DaytradingStrategy = _slot._strategy  # type: ignore[assignment]

                # Forzar cierre intradiario si aplica
                from datetime import datetime, time, timezone, timedelta
                _TZ_ARG = timezone(timedelta(hours=-3))
                now_arg = datetime.now(tz=_TZ_ARG).time()
                if dt._config.force_intraday_close and time(16, 45) <= now_arg < time(17, 0):
                    await dt.cerrar_todos(snapshot)
                else:
                    await dt.on_snapshot(snapshot)

                # Serializar indicadores y señal para la UI
                indicators = dt._last_indicators
                if not indicators:
                    # Durante warmup: mostrar progreso en la UI
                    min_data = max(dt._config.ema_lenta, dt._config.rsi_periodos + 1)
                    pts = len(dt._price_history)
                    remaining = min_data - pts
                    eta_min = (remaining * 30) / 60
                    _slot.add_log("INFO",
                        f"⏳ WARMUP {pts}/{min_data} snapshots — faltan ~{remaining} ticks (~{eta_min:.1f} min) "
                        f"para calcular EMA{dt._config.ema_rapida}/EMA{dt._config.ema_lenta}"
                    )
                else:
                    ema_fast  = indicators.get('ema_fast', 0)
                    ema_slow  = indicators.get('ema_slow', 0)
                    rsi       = indicators.get('rsi', 0)
                    spot_now  = indicators.get('spot', 0)
                    diff      = ema_fast - ema_slow
                    diff_pct  = (diff / spot_now * 100) if spot_now else 0

                    # Clasificar estado del cruce
                    if diff > 0:
                        trend_icon = "📈"
                        trend_label = "ALCISTA (EMA rápida arriba)"
                    else:
                        trend_icon = "📉"
                        trend_label = "BAJISTA (EMA rápida abajo)"

                    # Detectar si hay cruce inmiente (diff pequeño)
                    proximity = ""
                    if spot_now and abs(diff_pct) < 0.10:
                        proximity = " 🔄 CRUCE INMINENTE!"
                    elif spot_now and abs(diff_pct) < 0.20:
                        proximity = " ⚠️ EMAs convergiendo"

                    _slot.add_log("INFO",
                        f"{trend_icon} EMA{dt._config.ema_rapida}={ema_fast:.2f} "
                        f"EMA{dt._config.ema_lenta}={ema_slow:.2f} "
                        f"diff={diff:+.2f} ({diff_pct:+.2f}%) "
                        f"RSI={rsi:.1f} — {trend_label}{proximity}"
                    )

                if dt._last_signal:
                    sig = dt._last_signal
                    _slot.last_signals = [{
                        "simbolo": sig.opcion.quote.simbolo,
                        "lado": f"COMPRA {sig.direccion}",
                        "precio": sig.precio_limite,
                        "razon": sig.razon,
                        "score": round(sig.score, 4),
                    }]


            elif _slot.tipo_estrategia == "rsi_options":
                ro: RsiOptionsStrategy = _slot._strategy  # type: ignore[assignment]

                from datetime import datetime, time, timezone, timedelta
                _TZ_ARG = timezone(timedelta(hours=-3))
                now_arg = datetime.now(tz=_TZ_ARG).time()
                if ro._config.force_intraday_close and time(16, 45) <= now_arg < time(17, 0):
                    await ro.cerrar_todos(snapshot)
                else:
                    await ro.on_snapshot(snapshot)

                indicators = ro._last_indicators
                cfg_ro = ro._config
                if not indicators or indicators.get("warmup"):
                    n = indicators.get("candles_closed", 0) if indicators else 0
                    target = cfg_ro.rsi_periodos + 1
                    remaining = max(0, target - n)
                    eta_min = remaining * cfg_ro.candle_minutes
                    _slot.add_log("INFO",
                        f"⏳ WARMUP {n}/{target} velas {cfg_ro.candle_minutes}m — faltan ~{remaining} velas (~{eta_min} min) "
                        f"para calcular RSI{cfg_ro.rsi_periodos}"
                    )
                else:
                    rsi_val = indicators.get("rsi") or 0
                    spot_now = indicators.get("spot", 0)
                    tf = indicators.get("tf_min", cfg_ro.candle_minutes)
                    if rsi_val > cfg_ro.rsi_overbought:
                        zona = f"⬆️ SOBRECOMPRA (>{cfg_ro.rsi_overbought:.0f}) → señal PUT"
                    elif rsi_val < cfg_ro.rsi_oversold:
                        zona = f"⬇️ SOBREVENTA (<{cfg_ro.rsi_oversold:.0f}) → señal CALL"
                    else:
                        zona = "— zona neutral"
                    _slot.add_log("INFO",
                        f"📊 RSI{cfg_ro.rsi_periodos}({tf}m)={rsi_val:.1f} | spot={spot_now:.2f} {zona}"
                    )

                # Mostrar resultado del último intento de entrada
                attempt = ro._last_order_attempt
                if attempt:
                    resultado = attempt.get("resultado")
                    razon_attempt = attempt.get("razon", "")
                    if resultado == "ok":
                        _slot.add_log("INFO", f"✅ Orden enviada: {razon_attempt}")
                    elif resultado == "sin_opcion":
                        # Desplegar el desglose como múltiples líneas si está disponible
                        desglose = attempt.get("desglose") or []
                        if desglose:
                            for linea in desglose:
                                _slot.add_log("WARNING", linea)
                        else:
                            _slot.add_log("WARNING", f"⚠️ Señal RSI sin opción viable: {razon_attempt}")
                    elif resultado == "rechazada":
                        _slot.add_log("WARNING", f"🚫 {razon_attempt}")
                    elif resultado == "bloqueado":
                        _slot.add_log("INFO", f"⏸ {razon_attempt}")
                    ro._last_order_attempt = None  # limpiar para no re-loguear

                if ro._last_signal:
                    sig = ro._last_signal
                    _slot.last_signals = [{
                        "simbolo": sig.opcion.quote.simbolo,
                        "lado": f"COMPRA {sig.direccion}",
                        "precio": sig.precio_limite,
                        "razon": sig.razon,
                        "score": round(sig.score, 4),
                    }]

            else:
                # options_mispricing (default)
                pricings = enrich_snapshot(snapshot, r=DEFAULT_RISK_FREE_RATE)
                if pricings:
                    signals, stats = _slot._strategy.evaluar(pricings)
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
                            f"✅ {len(signals)} señal(es). Top: {signals[0].pricing.quote.simbolo} "
                            f"{signals[0].lado} score={signals[0].score:.3f}"
                        )
                        await _slot._strategy.ejecutar_signals(signals)
                    else:
                        # Mostrar breakdown de filtros en UI para diagnóstico
                        total = stats.get('total', 0)
                        if stats.get('sin_puntas', 0) == total:
                            _slot.add_log("WARNING",
                                f"🚫 Sin puntas: las {total} opciones tienen bid/ask=null — ¿fuera de horario?"
                            )
                        else:
                            partes = []
                            if stats.get('sin_puntas'):  partes.append(f"sin_puntas={stats['sin_puntas']}")
                            if stats.get('spread'):      partes.append(f"spread={stats['spread']}")
                            if stats.get('dte'):         partes.append(f"dte={stats['dte']}")
                            if stats.get('sin_iv'):      partes.append(f"sin_iv={stats['sin_iv']}")
                            if stats.get('delta'):       partes.append(f"delta={stats['delta']}")
                            if stats.get('mispricing'):  partes.append(f"mispricing<{int(_slot.config.get('min_mispricing_pct',0.05)*100)}%={stats['mispricing']}")
                            if stats.get('near_miss'):   partes.append(f"🔸 near_miss={stats['near_miss']}")
                            _slot.add_log("INFO",
                                f"📊 {total} opciones — sin señales. Filtrados: {', '.join(partes) or 'ninguno'}"
                            )

            # Broadcast a WebSocket
            await self._broadcast({
                "type": "snapshot",
                "slot_id": _slot.id,
                "data": _slot.to_dict(),
            })

        slot._snapshot_callback = _on_snapshot

        # ── Para acciones_ema registramos un callback dedicado en el StockDataFeed
        if slot.tipo_estrategia == "acciones_ema":
            ae: AccionesEMAStrategy = slot._strategy  # type: ignore[assignment]

            async def _on_stock_snapshot(stock_snap: StockSnapshot, _slot=slot) -> None:
                if _slot.estado != "running":
                    return

                # ── Fuera de horario de mercado: no analizar ──────────────
                from datetime import datetime, time as _t, timezone, timedelta
                _now_arg = datetime.now(tz=timezone(timedelta(hours=-3))).time()
                if not (_t(10, 30) <= _now_arg < _t(17, 0)):
                    return

                # Max Drawdown global
                max_drawdown = _slot.config.get("max_drawdown_ars", 0)
                if max_drawdown > 0:
                    slot_info = _slot.to_dict()
                    unrealized = sum(p.get("pnl_no_realizado") or 0 for p in slot_info.get("posiciones_abiertas", []))
                    realized   = slot_info.get("pnl_realizado", 0)
                    if (realized + unrealized) <= -max_drawdown:
                        _slot.add_log("CRITICAL",
                            f"⚠️ GLOBAL STOP LOSS: P&L {realized+unrealized:.2f} <= -{max_drawdown} ARS. Apagando..."
                        )
                        asyncio.create_task(self.stop_strategy(_slot.id))
                        return

                # EOD: cierre forzado a las 16:45
                from datetime import datetime, time, timezone, timedelta
                _TZ_ARG = timezone(timedelta(hours=-3))
                now_arg = datetime.now(tz=_TZ_ARG).time()
                _ae: AccionesEMAStrategy = _slot._strategy  # type: ignore[assignment]
                if _ae._config.force_close_eod and time(16, 45) <= now_arg < time(17, 0):
                    await _ae.cerrar_todos(stock_snap)
                else:
                    await _ae.on_snapshot(stock_snap)

                # Serializar indicadores para la UI
                indicators = _ae._last_indicators
                precio_actual = stock_snap.precio or 0

                if not indicators:
                    min_data = max(_ae._config.ema_lenta, _ae._config.rsi_periodos + 1)
                    pts = len(_ae._price_history)
                    remaining = min_data - pts
                    eta_min = (remaining * 30) / 60
                    _slot.add_log("INFO",
                        f"⏳ WARMUP {pts}/{min_data} — faltan ~{remaining} ticks (~{eta_min:.1f} min) "
                        f"para calcular EMA{_ae._config.ema_rapida}/EMA{_ae._config.ema_lenta} "
                        f"| {stock_snap.simbolo}={precio_actual:.2f}"
                    )
                else:
                    ema_fast = indicators.get("ema_fast", 0)
                    ema_slow = indicators.get("ema_slow", 0)
                    rsi      = indicators.get("rsi", 0)
                    diff     = ema_fast - ema_slow
                    diff_pct = (diff / precio_actual * 100) if precio_actual else 0

                    trend_icon  = "📈" if diff > 0 else "📉"
                    trend_label = "ALCISTA" if diff > 0 else "BAJISTA"
                    proximity   = ""
                    if precio_actual and abs(diff_pct) < 0.10:
                        proximity = " 🔄 CRUCE INMINENTE!"
                    elif precio_actual and abs(diff_pct) < 0.20:
                        proximity = " ⚠️ EMAs convergiendo"

                    _slot.add_log("INFO",
                        f"{trend_icon} {stock_snap.simbolo}=${precio_actual:.2f} | "
                        f"EMA{_ae._config.ema_rapida}={ema_fast:.2f} "
                        f"EMA{_ae._config.ema_lenta}={ema_slow:.2f} "
                        f"diff={diff:+.2f}({diff_pct:+.2f}%) "
                        f"RSI={rsi:.1f} — {trend_label}{proximity}"
                    )

                # Actualizar señal en UI
                if _ae._last_signal:
                    sig = _ae._last_signal
                    _slot.last_signals = [{
                        "simbolo": sig.simbolo,
                        "lado": "COMPRA ACCION",
                        "precio": sig.precio_limite,
                        "razon": sig.razon,
                        "score": round(sig.score, 4),
                    }]

                # Broadcast WebSocket
                await self._broadcast({
                    "type": "snapshot",
                    "slot_id": _slot.id,
                    "data": _slot.to_dict(),
                })

            slot._snapshot_callback = _on_stock_snapshot
            feed.on_snapshot(_on_stock_snapshot)
        else:
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

    async def get_portfolio_info(self) -> dict:
        """Obtiene el portafolio actual de la cuenta IOL."""
        if not self._client:
            return {"error": "No conectado"}
        try:
            return await self._client.get_portfolio(pais="argentina")
        except Exception as exc:
            logger.error("Error obteniendo portafolio: %s", exc)
            return {"error": str(exc)}

    async def get_operations_info(self) -> list:
        """Obtiene las operaciones recientes de la cuenta IOL."""
        if not self._client:
            return []
        try:
            return await self._client.get_operations(pais="argentina")
        except Exception as exc:
            logger.error("Error obteniendo operaciones: %s", exc)
            return []

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
