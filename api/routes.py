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
    ConfigureAIRequest,
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


@router.post("/reconnect")
async def reconnect_iol() -> dict:
    """Fuerza re-autenticación del cliente IOL."""
    engine = get_engine()
    ok = await engine.reconnect()
    if ok:
        return {"ok": True, "message": "Reconexión exitosa"}
    raise HTTPException(500, "Error al reconectar con IOL. Verificar credenciales y conectividad.")


@router.get("/account")
async def get_account() -> dict:
    engine = get_engine()
    return await engine.get_account_info()


@router.get("/portfolio")
async def get_portfolio() -> dict:
    engine = get_engine()
    return await engine.get_portfolio_info()


@router.get("/operations")
async def get_operations() -> list:
    engine = get_engine()
    return await engine.get_operations_info()


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


# ─── Claude AI Autopilot ─────────────────────────────────────────────────────

import os
import aiohttp
import re
import urllib.parse
import xml.etree.ElementTree as ET

async def fetch_merval_news(ticker: str) -> str:
    query = urllib.parse.quote(f"{ticker} acciones merval argentina")
    url = f"https://news.google.com/rss/search?q={query}&hl=es-419&gl=AR&ceid=AR:es-419"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    xml_data = await resp.text()
                    root = ET.fromstring(xml_data)
                    news_list = []
                    for i, item in enumerate(root.findall('.//item')[:5], 1):
                        title = item.find('title').text if item.find('title') is not None else ""
                        pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""
                        desc = item.find('description').text if item.find('description') is not None else ""
                        source_elem = item.find('source')
                        source = source_elem.text if source_elem is not None else "Diario"
                        # Limpiar etiquetas HTML del description
                        desc_clean = re.sub('<[^<]+>', '', desc).replace('\n', ' ').strip()
                        news_list.append(f"[Fuente {i}: {source} - {pub_date[:16]}]\nTitular: {title}\nResumen: {desc_clean}\n")
                    
                    if not news_list:
                        return "No hay noticias recientes de este activo en el feed."
                    return "\n".join(news_list)
    except Exception as e:
        logger.warning(f"No se pudieron extraer noticias RSS: {e}")
    return "Noticias no disponibles momentáneamente."

@router.post("/ai/configure")
async def analyze_and_configure_ai(req: ConfigureAIRequest):
    api_key = os.getenv("CLAUDE_API_KEY")
    if not api_key:
        raise HTTPException(500, "CLAUDE_API_KEY no encontrada en el .env")

    # Recuperar propiedades de la estrategia
    strategy_info = STRATEGY_TYPES.get(req.tipo_estrategia)
    if not strategy_info:
        raise HTTPException(400, "Tipo de estrategia inválido")

    # Extraer parámetros en formato amigable
    params_info = []
    for p in strategy_info["params"]:
        params_info.append(f"- {p['key']} ({p['type']}): default={p['default']}. Descripción: {p.get('descripcion', '')}")
    
    params_text = "\n".join(params_info)

    params_text = "\n".join(params_info)

    # RECOPILACIÓN RAG (Deep Research)
    engine = get_engine()
    live_quote_text = "No disponible"
    try:
        # Obtenemos la cotización real para el mercado y activo configurado
        quote_data = await engine._client.get_quote(req.mercado, req.activo)
        
        puntas = quote_data.get('puntas', [])
        b_q, b_p, a_p, a_q = 0, 0, 0, 0
        if isinstance(puntas, list) and len(puntas) > 0:
            first = puntas[0]
            b_q = first.get('cantidadCompra', 0)
            b_p = first.get('precioCompra', 0)
            a_p = first.get('precioVenta', 0)
            a_q = first.get('cantidadVenta', 0)
        elif isinstance(puntas, dict):
            b_q = puntas.get('cantidadCompra', 0)
            b_p = puntas.get('precioCompra', 0)
            a_p = puntas.get('precioVenta', 0)
            a_q = puntas.get('cantidadVenta', 0)

        live_quote_text = (
            f"Último operado: {quote_data.get('ultimoPrecio')} | "
            f"Variación Diaria: {quote_data.get('variacion')}% | "
            f"Apertura: {quote_data.get('apertura')} | "
            f"Volumen: {quote_data.get('volumen')} | "
            f"Puntas: Bid {b_q}x{b_p} - Ask {a_p}x{a_q}"
        )
    except Exception as e:
        logger.warning(f"No se pudo extraer cotización de {req.activo}: {e}")

    live_news_text = await fetch_merval_news(req.activo)

    system_prompt = (
        "Eres un analista cuantitativo experto del Mercado Merval Argentino y ByMA.\n"
        "Tu misión es analizar la tendencia de un activo bursátil y configurar los parámetros matemáticos "
        "óptimos para un bot algorítmico, asumiendo un nivel de riesgo inteligente.\n\n"
        f"Se te pedirá configurar una estrategia tipo '{strategy_info['nombre']}'.\n"
        f"La descripción técnica de esta estrategia es: {strategy_info['descripcion']}\n\n"
        "Los parámetros que *DEBES* configurar obligatoriamente en tu respuesta son:\n"
        f"{params_text}\n\n"
        "REGLA CRÍTICA DE RETORNO: Tu output DEBE SER ÚNICA Y ESTRICTAMENTE un JSON válido, sin delimitadores ```json, sin saltos de linea extraños y sin NINGÚN texto adicional antes ni después del bloque JSON.\n"
        "Estructura exigida:\n"
        "{\n"
        '  "analysis": "Un único párrafo de texto detallado. DEBES CITAR OBLIGATORIAMENTE LAS FUENTES (ej. ségun [Fuente 1: Cronista]) que usaste de las noticias provistas, y fundamentar el sesgo adoptado y los parámetros.",\n'
        '  "config": {\n'
        '    "clave_parametro_1": valor_1,\n'
        '    "clave_parametro_N": valor_n\n'
        "  }\n"
        "}"
    )

    prompt = (
        f"Realiza un análisis algorítmico express sobre el activo: {req.activo}.\n"
        f"El usuario proyecta asignar {req.fondos_asignados} ARS de fondeo (0 significa capital libre).\n\n"
        "=== [LIVE RESEARCH DATA DE ESTE INSTANTE] ===\n"
        f"Cotización Actual de {req.activo}: {live_quote_text}\n"
        f"\nTitulares Destacados Recientes (Google News / Merval):\n{live_news_text}\n"
        "===========================================\n\n"
        f"Alimenta tu bloque interno de 'thinking' analizando esta data real.\n"
        f"Devuelve el JSON con la configuración ('config') completa ajustada y el bloque 'analysis' final."
    )

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 4096,
        "thinking": {"type": "enabled", "budget_tokens": 2048},
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Error de Claude API: {text}")
                    raise HTTPException(502, "Error comunicándose con el motor de IA de Claude.")
                
                data = await resp.json()
                
                # Buscar el bloque de texto (ignorar el bloque de 'thinking')
                content = ""
                for blk in data.get("content", []):
                    if blk.get("type") == "text":
                        content = blk.get("text", "")
                        break
                
                if not content:
                    raise ValueError("Claude no devolvió bloque de texto válido.")
                
                # Intentar limpiar la posible markdown formatting
                if content.startswith("```json"):
                    content = content[7:]
                if content.startswith("```"):
                    content = content[3:]
                content = content.strip()
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()

                try:
                    parsed_json = json.loads(content)
                    return parsed_json
                except json.JSONDecodeError:
                    logger.error(f"Claude devolvió un JSON inválido: {content}")
                    raise HTTPException(502, "La IA no devolvió un formato JSON válido.")
        except Exception as e:
            logger.exception("Error contactando AI Auth")
            raise HTTPException(502, f"Falló conexión externa: {str(e)}")


@router.post("/strategies/{slot_id}/stop")
async def stop_strategy(slot_id: str) -> dict:
    engine = get_engine()
    await engine.stop_strategy(slot_id)
    slot = engine.get_slot(slot_id)
    if not slot:
        raise HTTPException(404, f"Estrategia {slot_id} no encontrada")
    return slot


# ─── Trading Manual ──────────────────────────────────────────────────────────

from pydantic import BaseModel

class ManualQuoteRequest(BaseModel):
    mercado: str = "bCBA"
    simbolo: str

class ManualOrderRequest(BaseModel):
    mercado: str = "bCBA"
    simbolo: str
    operacion: str  # "compra" | "venta"
    cantidad: int
    precio: float
    plazo: str = "t0"

@router.get("/trading/puede-operar")
async def check_puede_operar() -> dict:
    """Verifica si la operatoria está habilitada para la cuenta (GET /api/v2/operar/CPD/PuedeOperar)."""
    engine = get_engine()
    try:
        result = await asyncio.wait_for(engine._client.puede_operar(), timeout=15.0)
        return {"ok": True, "operatoriaHabilitada": result.get("operatoriaHabilitada", False), "raw": result}
    except asyncio.TimeoutError:
        raise HTTPException(504, "IOL no respondió al verificar operatoria.")
    except Exception as e:
        raise HTTPException(400, f"Error al verificar operatoria: {str(e)}")

@router.post("/trading/quote")
async def manual_get_quote(req: ManualQuoteRequest) -> dict:
    """Cotización manual de cualquier instrumento."""
    engine = get_engine()
    try:
        data = await engine._client.get_quote(req.mercado, req.simbolo)
        return {"ok": True, "data": data}
    except Exception as e:
        raise HTTPException(400, f"Error al obtener cotización: {str(e)}")

@router.post("/trading/order")
async def manual_place_order(req: ManualOrderRequest) -> dict:
    """Envía una orden manual al mercado. Timeout: 40s para no colgar al cliente."""
    engine = get_engine()
    try:
        result = await asyncio.wait_for(
            engine._client.place_order(
                mercado=req.mercado,
                simbolo=req.simbolo,
                operacion=req.operacion,
                cantidad=req.cantidad,
                precio=req.precio,
                plazo=req.plazo,
            ),
            timeout=40.0,
        )
        return {"ok": True, "data": result}
    except asyncio.TimeoutError:
        logger.error("Timeout enviando orden a IOL (>40s): %s %s x%d @ %.2f",
                     req.operacion, req.simbolo, req.cantidad, req.precio)
        raise HTTPException(504, "IOL no respondió en 40s. La orden puede o no haberse enviado. Verificá tu panel de operaciones en IOL antes de reintentar.")
    except Exception as e:
        raise HTTPException(400, f"Error al enviar orden: {str(e)}")

@router.delete("/trading/order/{order_id}")
async def manual_cancel_order(order_id: int) -> dict:
    """Cancela una orden manual."""
    engine = get_engine()
    try:
        result = await asyncio.wait_for(
            engine._client.cancel_order(order_id),
            timeout=40.0,
        )
        return {"ok": True, "data": result}
    except asyncio.TimeoutError:
        raise HTTPException(504, "IOL no respondió en 40s al cancelar la orden. Verificá tu panel en IOL.")
    except Exception as e:
        raise HTTPException(400, f"Error al cancelar orden: {str(e)}")

@router.get("/trading/operations")
async def manual_get_operations() -> dict:
    """Obtiene las operaciones recientes."""
    engine = get_engine()
    try:
        result = await engine._client.get_operations()
        return {"ok": True, "data": result}
    except Exception as e:
        raise HTTPException(400, f"Error al obtener operaciones: {str(e)}")

@router.get("/trading/panel/{instrumento}/{panel}")
async def get_panel(instrumento: str, panel: str) -> dict:
    """Panel de cotizaciones (acciones, bonos, opciones, cedears)."""
    engine = get_engine()
    try:
        data = await engine._client.get_cotizaciones_panel(instrumento, panel, "argentina")
        return {"ok": True, "data": data}
    except Exception as e:
        raise HTTPException(400, f"Error al obtener panel: {str(e)}")


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
