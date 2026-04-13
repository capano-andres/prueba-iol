"""
Módulo 1: Cliente API IOL v2
- Autenticación OAuth2 con renovación automática de token
- Exponential Backoff with Jitter para manejo de HTTP 429
"""

import asyncio
import logging
import random
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://api.invertironline.com"
TOKEN_URL = f"{BASE_URL}/token"
API_V2 = f"{BASE_URL}/api/v2"

# Rangos de delay para cada intento (min_ms, max_ms)
BACKOFF_RANGES = [
    (500, 1000),
    (1000, 2000),
    (2000, 4000),
    (4000, 8000),
    (8000, 16000),
]
MAX_RETRIES = 5


class IOLAuthError(Exception):
    pass


class IOLRequestError(Exception):
    pass


class IOLClient:
    """
    Cliente asíncrono para la API v2 de InvertirOnline.

    Uso:
        async with IOLClient(username, password) as client:
            data = await client.get_quote("bCBA", "GGAL")
    """

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._session: aiohttp.ClientSession | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires_at: float = 0.0
        self._refresh_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    # Context manager
    # ------------------------------------------------------------------ #

    async def __aenter__(self) -> "IOLClient":
        self._session = aiohttp.ClientSession()
        try:
            await self.authenticate()
        except Exception:
            await self._session.close()
            self._session = None
            raise
        return self

    async def __aexit__(self, *_) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
        if self._session:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------ #
    # Autenticación OAuth2
    # ------------------------------------------------------------------ #

    async def authenticate(self) -> None:
        """Obtiene access_token + refresh_token y lanza la tarea de renovación."""
        payload = {
            "username": self._username,
            "password": self._password,
            "grant_type": "password",
        }
        last_error = None
        for attempt, (min_ms, max_ms) in enumerate(BACKOFF_RANGES[:3], start=1):
            async with self._session.post(
                TOKEN_URL,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    break
                body = await resp.text()
                last_error = f"HTTP {resp.status}: {body}"
                # Sólo reintentar en errores transitorios del servidor
                if resp.status < 500:
                    raise IOLAuthError(f"Autenticación fallida ({last_error})")
                delay = random.randint(min_ms, max_ms) / 1000.0
                logger.warning(
                    "Auth falló (%s) – intento %d/3. Esperando %.1f s…",
                    last_error, attempt, delay,
                )
                await asyncio.sleep(delay)
        else:
            raise IOLAuthError(f"Autenticación fallida tras 3 intentos. Último error: {last_error}")

        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token")
        expires_in = int(data.get("expires_in", 1800))
        self._token_expires_at = time.monotonic() + expires_in
        logger.info("Token obtenido. Expira en %d s.", expires_in)

        # Lanzar renovación automática 60 s antes de vencer
        self._refresh_task = asyncio.create_task(
            self._auto_refresh(expires_in - 60)
        )

    async def _auto_refresh(self, wait_seconds: int) -> None:
        """Tarea en segundo plano: refresca el token antes de que expire."""
        await asyncio.sleep(max(wait_seconds, 30))
        logger.info("Renovando token de acceso...")

        # Reintentar hasta 3 veces con 30s de pausa ante errores transitorios
        for intento in range(1, 4):
            try:
                if self._refresh_token:
                    await self._do_refresh()
                else:
                    await self.authenticate()
                return  # exito
            except Exception as exc:
                logger.error(
                    "Error al renovar token (intento %d/3): %s", intento, exc
                )
                if intento < 3:
                    logger.info("Reintentando renovacion en 30 s...")
                    await asyncio.sleep(30)

        logger.critical(
            "No se pudo renovar el token tras 3 intentos. "
            "Los requests de mercado fallaran hasta el proximo ciclo. "
            "Verificar credenciales y conectividad."
        )

    async def _do_refresh(self) -> None:
        """Usa el refresh_token para obtener un nuevo access_token."""
        payload = {
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        }
        async with self._session.post(
            TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            if resp.status != 200:
                logger.warning(
                    "Refresh falló (HTTP %d), re-autenticando…", resp.status
                )
                await self.authenticate()
                return
            data = await resp.json(content_type=None)

        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        expires_in = int(data.get("expires_in", 1800))
        self._token_expires_at = time.monotonic() + expires_in
        logger.info("Token renovado. Expira en %d s.", expires_in)

        # Reprogramar renovación
        self._refresh_task = asyncio.create_task(
            self._auto_refresh(expires_in - 60)
        )

    # ------------------------------------------------------------------ #
    # HTTP con Exponential Backoff + Jitter
    # ------------------------------------------------------------------ #

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._access_token}"}

    async def _request(
        self, method: str, url: str, **kwargs
    ) -> Any:
        """
        Ejecuta una petición HTTP con Exponential Backoff with Jitter
        ante respuestas HTTP 429 (Too Many Requests), 502/503/504 o errores de red.
        """
        import aiohttp
        for attempt, (min_ms, max_ms) in enumerate(BACKOFF_RANGES, start=1):
            headers = {**self._auth_headers(), **kwargs.pop("headers", {})}
            try:
                async with self._session.request(
                    method, url, headers=headers, **kwargs
                ) as resp:
                    if resp.status == 429 or resp.status >= 500:
                        # Respetar Retry-After si viene en la respuesta o error de servidor
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after and resp.status == 429:
                            delay = float(retry_after)
                        else:
                            delay = random.randint(min_ms, max_ms) / 1000.0
                        logger.warning(
                            "HTTP %d – intento %d/%d. Esperando %.2f s…",
                            resp.status,
                            attempt,
                            MAX_RETRIES,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    if resp.status >= 400:
                        body = await resp.text()
                        raise IOLRequestError(
                            f"HTTP {resp.status} en {url}: {body}"
                        )

                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                delay = random.randint(min_ms, max_ms) / 1000.0
                logger.warning(
                    "Network error (%s) – intento %d/%d. Esperando %.2f s…",
                    type(exc).__name__, attempt, MAX_RETRIES, delay
                )
                if attempt == MAX_RETRIES:
                    raise IOLRequestError(f"Error de red tras {MAX_RETRIES} reintentos: {str(exc)}")
                await asyncio.sleep(delay)

        raise IOLRequestError(
            f"Se superaron {MAX_RETRIES} reintentos para {url}. Operación descartada."
        )

    async def get(self, path: str, **kwargs) -> Any:
        return await self._request("GET", f"{API_V2}{path}", **kwargs)

    async def post(self, path: str, **kwargs) -> Any:
        return await self._request("POST", f"{API_V2}{path}", **kwargs)

    async def delete(self, path: str, **kwargs) -> Any:
        return await self._request("DELETE", f"{API_V2}{path}", **kwargs)

    # ------------------------------------------------------------------ #
    # Endpoints de mercado  (rutas verificadas contra Swagger v2)
    # ------------------------------------------------------------------ #

    async def get_profile(self) -> dict:
        """Datos del perfil del usuario (no requiere TyC API)."""
        return await self.get("/datos-perfil")

    async def get_quote(self, mercado: str, simbolo: str) -> dict:
        """
        Cotización de un instrumento.
        mercado: bCBA | nYSE | nASDAQ | bELB | etc.
        simbolo: GGAL, YPFD, etc.
        Endpoint: GET /api/v2/{mercado}/Titulos/{simbolo}/Cotizacion
        """
        return await self.get(f"/{mercado}/Titulos/{simbolo}/Cotizacion")

    async def get_options_chain(self, mercado: str, simbolo: str) -> list:
        """
        Cadena completa de opciones de un subyacente.
        Endpoint: GET /api/v2/{mercado}/Titulos/{simbolo}/Opciones
        """
        return await self.get(f"/{mercado}/Titulos/{simbolo}/Opciones")

    async def get_portfolio(self, pais: str = "argentina") -> dict:
        """
        Portafolio de la cuenta.
        pais: argentina | estados_unidos
        Endpoint: GET /api/v2/portafolio/{pais}
        """
        return await self.get(f"/portafolio/{pais}")

    async def get_account_state(self) -> dict:
        """Saldo disponible y estado general de la cuenta comitente."""
        return await self.get("/estadocuenta")

    async def get_operations(self, estado: str | None = None,
                             fecha_desde: str | None = None,
                             fecha_hasta: str | None = None,
                             pais: str | None = None) -> list:
        """
        Historial de operaciones con filtros opcionales.
        estado: pendiente | terminada | cancelada | rechazada
        fecha_desde / fecha_hasta: formato YYYY-MM-DD
        """
        params: dict = {}
        if estado:
            params["filtro.estado"] = estado
        if fecha_desde:
            params["filtro.fechaDesde"] = fecha_desde
        if fecha_hasta:
            params["filtro.fechaHasta"] = fecha_hasta
        if pais:
            params["filtro.pais"] = pais
        return await self.get("/operaciones", params=params)

    async def get_cotizaciones_panel(self, instrumento: str,
                                     panel: str, pais: str) -> list:
        """
        Panel de cotizaciones por instrumento.
        instrumento: acciones | bonos | opciones | cedears | letras | etc.
        panel: lider | merval | general | etc.
        pais: argentina
        Endpoint: GET /api/v2/Cotizaciones/{Instrumento}/{Panel}/{Pais}
        """
        return await self.get(f"/Cotizaciones/{instrumento}/{panel}/{pais}")

    async def get_titulo(self, mercado: str, simbolo: str) -> dict:
        """Info completa de un título (no solo cotización)."""
        return await self.get(f"/{mercado}/Titulos/{simbolo}")

    # ------------------------------------------------------------------ #
    # Endpoints de trading  (operar/v2/Ordenes)
    # ------------------------------------------------------------------ #

    async def place_order(
        self,
        mercado:   str,
        simbolo:   str,
        operacion: str,       # "compra" | "venta"
        cantidad:  int,
        precio:    float,
        plazo:     str = "t0",
        validez:   str = "DIAVALIDEZ",
    ) -> dict:
        """
        Envía una orden limitada al mercado.

        Parámetros
        ----------
        operacion : "compra" | "venta"
        plazo     : "t0" (contado inmediato) | "t1" | "t2"
        validez   : "DIAVALIDEZ" (válida hasta el cierre del día)
        """
        payload = {
            "mercado":   mercado,
            "simbolo":   simbolo,
            "cantidad":  cantidad,
            "precio":    precio,
            "plazo":     plazo,
            "validez":   validez,
            "operacion": operacion,
        }
        return await self.post("/operar/v2/Ordenes", json=payload)

    async def cancel_order(self, order_id: int) -> dict:
        """Cancela una orden por su ID."""
        return await self.delete(f"/operar/v2/Ordenes/{order_id}")

    async def get_order(self, order_id: int) -> dict:
        """Estado actualizado de una orden por su ID."""
        return await self.get(f"/operaciones/{order_id}")
