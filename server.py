"""
Servidor principal — FastAPI + Uvicorn.

Arranca el TradingEngine, monta las rutas API y sirve el frontend.

Ejecutar:
    python server.py
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import router as api_router, ws_router, set_engine
from engine import TradingEngine

# ─── Logging ──────────────────────────────────────────────────────────────────

# Force UTF-8 on Windows (cp1252 can't encode emojis in log messages)
_handler = logging.StreamHandler(
    open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)
)
_handler.setFormatter(logging.Formatter(
    fmt="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
    datefmt="%H:%M:%S",
))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
logger = logging.getLogger("server")

# ─── Engine global ────────────────────────────────────────────────────────────

engine = TradingEngine()


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa el engine al arrancar y lo apaga al cerrar."""
    logger.info("Iniciando TradingEngine…")
    try:
        await engine.initialize()
        set_engine(engine)
        logger.info("TradingEngine listo.")
    except Exception as exc:
        logger.error("Error inicializando engine: %s", exc)
        logger.warning("El servidor arrancará sin conexión a IOL.")
        set_engine(engine)
    yield
    logger.info("Apagando TradingEngine…")
    await engine.shutdown()
    logger.info("Servidor detenido.")


# ─── App FastAPI ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="IOL Trading Platform",
    description="Plataforma multi-estrategia de trading algorítmico via IOL API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS para desarrollo (Vite en :5173)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Montar rutas
app.include_router(api_router)
app.include_router(ws_router)

# Servir frontend en producción (si existe el build)
frontend_dist = Path(__file__).parent / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
    logger.info("Frontend servido desde %s", frontend_dist)


# ─── Punto de entrada ────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
