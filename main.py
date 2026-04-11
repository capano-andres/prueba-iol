"""
Loop de producción: ensambla los 5 módulos del bot de opciones GGAL.

Ejecutar:
    python main.py

Variables de entorno (.env):
    IOL_USERNAME  — usuario IOL
    IOL_PASSWORD  — contraseña IOL

El bot corre en modo DRY-RUN por defecto. Para operar en vivo cambiar
DRY_RUN = False SOLO luego de validación exhaustiva en dry-run.
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from iol_client import IOLClient
from market_data import MarketDataFeed
from oms import OMS, IOLProfile
from strategy import Strategy, StrategyConfig

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s %(levelname)-8s %(name)s - %(message)s",
    datefmt = "%H:%M:%S",
    stream  = sys.stdout,
)

logger = logging.getLogger("main")

# ─── Configuración ────────────────────────────────────────────────────────────

POLL_INTERVAL = 30.0   # segundos entre consultas a la API de IOL
DRY_RUN       = True   # cambiar a False SOLO para operar en vivo


# ─── Loop principal ───────────────────────────────────────────────────────────

async def run() -> None:
    load_dotenv()
    username = os.getenv("IOL_USERNAME")
    password = os.getenv("IOL_PASSWORD")

    if not username or not password:
        logger.error(
            "Faltan credenciales IOL. "
            "Verificar .env (IOL_USERNAME, IOL_PASSWORD)."
        )
        return

    mode = "DRY-RUN" if DRY_RUN else "LIVE *** ORDENES REALES ***"
    logger.info("Iniciando bot GGAL opciones — modo %s", mode)

    async with IOLClient(username, password) as client:

        feed     = MarketDataFeed(client, interval=POLL_INTERVAL)
        oms      = OMS(client, mercado="bCBA", profile=IOLProfile.GOLD, dry_run=DRY_RUN)
        strategy = Strategy(oms, StrategyConfig())

        # Conectar callbacks en orden:
        # OMS primero (actualiza estado de órdenes y dispara cierre automático),
        # luego Strategy (evalúa y abre posiciones nuevas).
        feed.on_snapshot(oms.on_snapshot)
        feed.on_snapshot(strategy.on_snapshot)

        # Tarea de cierre automático a las 16:45 ARG
        oms.start_auto_close_scheduler()

        # Iniciar polling del mercado
        await feed.start()
        logger.info(
            "Feed iniciado. Intervalo: %.0f s. Ctrl+C para detener.", POLL_INTERVAL
        )

        try:
            await asyncio.Event().wait()   # corre indefinidamente hasta Ctrl+C
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            logger.info("Deteniendo feed...")
            await feed.stop()
            pnl = oms.pnl_realizado_total()
            logger.info("Bot detenido. P&L realizado del dia: %.2f ARS", pnl)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
