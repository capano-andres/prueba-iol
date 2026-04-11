"""
Rutas API REST y WebSocket para la plataforma de trading.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException

from api.models import (
    CreateStrategyRequest,
    UpdateStrategyRequest,
    StatusResponse,
)
from engine import TradingEngine, STRATEGY_TYPES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# ─── Inyección del engine (se setea desde server.py) ─────────────────────────

_engine: TradingEngine | None = None


def set_engine(engine: TradingEngine) -> None:
    global _engine
    _engine = engine


def get_engine() -> TradingEngine:
    if _engine is None:
        raise HTTPException(503, "Engine no inicializado")
    return _engine


# ─── Endpoints de estado ─────────────────────────────────────────────────────

@router.get("/status")
async def get_status() -> dict:
    engine = get_engine()
    slots = engine.get_all_slots()
    running = [s for s in slots if s["estado"] == "running"]
    return {
        "connected": engine.connected,
        "n_strategies": len(slots),
        "n_running": len(running),
        "pnl_total": sum(s.get("pnl_realizado", 0) for s in slots),
    }


@router.get("/account")
async def get_account() -> dict:
    engine = get_engine()
    return await engine.get_account_info()


@router.get("/strategy-types")
async def get_strategy_types() -> dict:
    return STRATEGY_TYPES


# ─── CRUD de estrategias ─────────────────────────────────────────────────────

@router.get("/strategies")
async def list_strategies() -> list[dict]:
    engine = get_engine()
    return engine.get_all_slots()


@router.post("/strategies")
async def create_strategy(req: CreateStrategyRequest) -> dict:
    engine = get_engine()
    slot_id = await engine.add_strategy(req.model_dump())
    slot = engine.get_slot(slot_id)
    return slot


@router.get("/strategies/{slot_id}")
async def get_strategy(slot_id: str) -> dict:
    engine = get_engine()
    slot = engine.get_slot(slot_id)
    if not slot:
        raise HTTPException(404, f"Estrategia {slot_id} no encontrada")
    return slot


@router.put("/strategies/{slot_id}")
async def update_strategy(slot_id: str, req: UpdateStrategyRequest) -> dict:
    engine = get_engine()
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        result = await engine.update_strategy(slot_id, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not result:
        raise HTTPException(404, f"Estrategia {slot_id} no encontrada")
    return result


@router.delete("/strategies/{slot_id}")
async def delete_strategy(slot_id: str) -> dict:
    engine = get_engine()
    ok = await engine.remove_strategy(slot_id)
    if not ok:
        raise HTTPException(404, f"Estrategia {slot_id} no encontrada")
    return {"ok": True, "id": slot_id}


# ─── Control de ejecución ────────────────────────────────────────────────────

@router.post("/strategies/{slot_id}/start")
async def start_strategy(slot_id: str) -> dict:
    engine = get_engine()
    try:
        await engine.start_strategy(slot_id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return engine.get_slot(slot_id)


@router.post("/strategies/{slot_id}/pause")
async def pause_strategy(slot_id: str) -> dict:
    engine = get_engine()
    await engine.pause_strategy(slot_id)
    slot = engine.get_slot(slot_id)
    if not slot:
        raise HTTPException(404, f"Estrategia {slot_id} no encontrada")
    return slot


@router.post("/strategies/{slot_id}/stop")
async def stop_strategy(slot_id: str) -> dict:
    engine = get_engine()
    await engine.stop_strategy(slot_id)
    slot = engine.get_slot(slot_id)
    if not slot:
        raise HTTPException(404, f"Estrategia {slot_id} no encontrada")
    return slot


# ─── Logs ────────────────────────────────────────────────────────────────────

@router.get("/strategies/{slot_id}/logs")
async def get_strategy_logs(slot_id: str, limit: int = 50) -> list[dict]:
    engine = get_engine()
    return engine.get_slot_logs(slot_id, limit)


# ─── WebSocket ───────────────────────────────────────────────────────────────

ws_router = APIRouter()


@ws_router.websocket("/ws/live")
async def websocket_live(ws: WebSocket) -> None:
    await ws.accept()
    engine = get_engine()

    async def send_data(data: dict) -> None:
        try:
            await ws.send_json(data)
        except Exception:
            pass

    engine.register_ws(send_data)
    logger.info("WebSocket conectado.")

    try:
        # Enviar estado inicial
        await ws.send_json({
            "type": "init",
            "strategies": engine.get_all_slots(),
        })

        # Mantener conexión abierta escuchando mensajes del cliente
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                # El cliente puede enviar pings o solicitudes
                data = json.loads(msg)
                if data.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                # Enviar heartbeat
                await ws.send_json({"type": "heartbeat"})
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        engine.unregister_ws(send_data)
        logger.info("WebSocket desconectado.")
