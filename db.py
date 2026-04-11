"""
Módulo de persistencia SQLite.

Almacena configuraciones de StrategySlots y historial de operaciones
para garantizar continuidad entre reinicios del servidor.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "trading_platform.db")

# ─── SQL de creación ──────────────────────────────────────────────────────────

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS strategy_slots (
    id              TEXT PRIMARY KEY,
    nombre          TEXT NOT NULL,
    tipo_estrategia TEXT NOT NULL,
    activo          TEXT NOT NULL,
    mercado         TEXT NOT NULL DEFAULT 'bCBA',
    fondos_asignados REAL NOT NULL DEFAULT 0,
    config_json     TEXT NOT NULL DEFAULT '{}',
    dry_run         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions_history (
    id              TEXT PRIMARY KEY,
    slot_id         TEXT NOT NULL,
    simbolo         TEXT NOT NULL,
    tipo            TEXT NOT NULL,
    lado            TEXT NOT NULL,
    cantidad        INTEGER NOT NULL,
    precio_apertura REAL NOT NULL,
    comision_apertura REAL NOT NULL DEFAULT 0,
    precio_cierre   REAL,
    comision_cierre REAL,
    pnl_realizado   REAL,
    fecha_apertura  TEXT NOT NULL,
    fecha_cierre    TEXT,
    FOREIGN KEY (slot_id) REFERENCES strategy_slots(id)
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id TEXT NOT NULL,
    fecha   TEXT NOT NULL,
    pnl     REAL NOT NULL DEFAULT 0,
    operaciones INTEGER NOT NULL DEFAULT 0,
    UNIQUE(slot_id, fecha),
    FOREIGN KEY (slot_id) REFERENCES strategy_slots(id)
);
"""


# ─── Funciones de acceso ─────────────────────────────────────────────────────

async def init_db() -> None:
    """Crea las tablas si no existen."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_CREATE_TABLES)
        await db.commit()
    logger.info("Base de datos inicializada: %s", DB_PATH)


# ── Strategy Slots ────────────────────────────────────────────────────────────

async def save_slot(slot: dict) -> None:
    """Inserta o reemplaza un StrategySlot."""
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO strategy_slots
               (id, nombre, tipo_estrategia, activo, mercado,
                fondos_asignados, config_json, dry_run, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                slot["id"],
                slot["nombre"],
                slot["tipo_estrategia"],
                slot["activo"],
                slot.get("mercado", "bCBA"),
                slot.get("fondos_asignados", 0),
                json.dumps(slot.get("config", {})),
                1 if slot.get("dry_run", True) else 0,
                slot.get("created_at", now),
                now,
            ),
        )
        await db.commit()


async def load_all_slots() -> list[dict]:
    """Carga todos los slots guardados."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM strategy_slots ORDER BY created_at")
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"],
                "nombre": r["nombre"],
                "tipo_estrategia": r["tipo_estrategia"],
                "activo": r["activo"],
                "mercado": r["mercado"],
                "fondos_asignados": r["fondos_asignados"],
                "config": json.loads(r["config_json"]),
                "dry_run": bool(r["dry_run"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]


async def delete_slot(slot_id: str) -> bool:
    """Elimina un slot. Retorna True si existía."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM strategy_slots WHERE id = ?", (slot_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


# ── Historial de posiciones ──────────────────────────────────────────────────

async def save_position(slot_id: str, pos: dict) -> None:
    """Guarda una posición cerrada en el historial."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO positions_history
               (id, slot_id, simbolo, tipo, lado, cantidad,
                precio_apertura, comision_apertura, precio_cierre,
                comision_cierre, pnl_realizado, fecha_apertura, fecha_cierre)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pos["id"],
                slot_id,
                pos["simbolo"],
                pos["tipo"],
                pos["lado"],
                pos["cantidad"],
                pos["precio_apertura"],
                pos.get("comision_apertura", 0),
                pos.get("precio_cierre"),
                pos.get("comision_cierre"),
                pos.get("pnl_realizado"),
                pos["fecha_apertura"],
                pos.get("fecha_cierre"),
            ),
        )
        await db.commit()


async def get_positions_history(
    slot_id: str, limit: int = 50
) -> list[dict]:
    """Retorna las últimas N posiciones cerradas de un slot."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM positions_history
               WHERE slot_id = ?
               ORDER BY fecha_cierre DESC
               LIMIT ?""",
            (slot_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ── P&L diario ──────────────────────────────────────────────────────────────

async def upsert_daily_pnl(
    slot_id: str, fecha: str, pnl: float, operaciones: int
) -> None:
    """Actualiza o crea el registro de P&L diario de un slot."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO daily_pnl (slot_id, fecha, pnl, operaciones)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(slot_id, fecha)
               DO UPDATE SET pnl = ?, operaciones = ?""",
            (slot_id, fecha, pnl, operaciones, pnl, operaciones),
        )
        await db.commit()


async def get_daily_pnl(slot_id: str, days: int = 30) -> list[dict]:
    """Retorna los últimos N días de P&L de un slot."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM daily_pnl
               WHERE slot_id = ?
               ORDER BY fecha DESC
               LIMIT ?""",
            (slot_id, days),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
