"""
Módulo 2: Market Data
- Polling de la cadena de opciones GGAL (configurable)
- Parser de tickers ByMA  →  tipo / strike / vencimiento
- Snapshots tipados para consumir desde el Motor Matemático (M3) y el OMS (M4)
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Awaitable, Callable

from iol_client import IOLClient, IOLRequestError

logger = logging.getLogger(__name__)

# ─── Constantes ──────────────────────────────────────────────────────────────

MERCADO    = "bCBA"
SUBYACENTE = "GGAL"

# Códigos de mes ByMA
#   Calls  A-L  →  ene-dic  (A=1 … L=12)
#   Puts   M-X  →  ene-dic  (M=1 … X=12)
_CALL_MONTHS: dict[str, int] = {c: i + 1 for i, c in enumerate("ABCDEFGHIJKL")}
_PUT_MONTHS:  dict[str, int] = {c: i + 1 for i, c in enumerate("MNOPQRSTUVWX")}

# Regex: {base ≥2 mayúsculas}{C|V}{dígitos del strike}{código de mes}
# Ej: GFGC11324J  →  base=GFG  tipo=C  strike=11324  mes=J(oct)
_TICKER_RE = re.compile(
    r"^(?P<base>[A-Z]{2,})(?P<tipo>[CV])(?P<strike>\d+(?:\.\d+)?)(?P<mes>[A-X])$"
)

# ─── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass(slots=True)
class OptionQuote:
    """Cotización instantánea de un contrato de opción."""
    simbolo:   str
    tipo:      str          # "CALL" | "PUT"
    strike:    float
    expiry:    date
    bid:       float | None
    ask:       float | None
    ultimo:    float | None
    volumen:   int   | None
    timestamp: float = field(default_factory=time.monotonic)

    @property
    def mid(self) -> float | None:
        """Precio medio (bid+ask)/2; fallback al último precio."""
        if self.bid and self.ask and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.ultimo

    @property
    def spread_pct(self) -> float | None:
        """Spread relativo (ask-bid)/mid."""
        m = self.mid
        if m and m > 0 and self.bid is not None and self.ask is not None:
            return (self.ask - self.bid) / m
        return None

    @property
    def dias_al_vencimiento(self) -> int:
        return (self.expiry - date.today()).days


@dataclass(slots=True)
class MarketSnapshot:
    """Foto completa del mercado: subyacente + cadena de opciones."""
    ts:       datetime
    spot:     float | None
    opciones: list[OptionQuote]

    def calls(self) -> list[OptionQuote]:
        return [o for o in self.opciones if o.tipo == "CALL"]

    def puts(self) -> list[OptionQuote]:
        return [o for o in self.opciones if o.tipo == "PUT"]

    def by_expiry(self, expiry: date) -> list[OptionQuote]:
        return [o for o in self.opciones if o.expiry == expiry]

    def expiries(self) -> list[date]:
        return sorted({o.expiry for o in self.opciones})

    def resumen(self) -> str:
        calls = len(self.calls())
        puts  = len(self.puts())
        vctos = len(self.expiries())
        return (
            f"[{self.ts:%H:%M:%S}]  spot={self.spot or 'N/D':>10}  "
            f"opciones={len(self.opciones):>3}  "
            f"(calls={calls} puts={puts})  vencimientos={vctos}"
        )


# ─── Parser de tickers ByMA ──────────────────────────────────────────────────

def parse_ticker(simbolo: str) -> tuple[str, float, date] | None:
    """
    Parsea un ticker de opción ByMA.

    Retorna (tipo, strike, expiry) o None si el formato no es reconocido.

    Ejemplos
    --------
    >>> parse_ticker("GFGC11324J")
    ('CALL', 11324.0, date(2026, 10, 16))
    >>> parse_ticker("GFGV8500P")
    ('PUT', 8500.0, date(2026, 4, 17))
    """
    m = _TICKER_RE.match(simbolo.upper().strip())
    if not m:
        return None

    tipo_char = m.group("tipo")
    mes_code  = m.group("mes")

    try:
        strike = float(m.group("strike"))
    except ValueError:
        return None

    if tipo_char == "C":
        tipo = "CALL"
        mes  = _CALL_MONTHS.get(mes_code)
    else:
        tipo = "PUT"
        mes  = _PUT_MONTHS.get(mes_code)

    if mes is None:
        return None

    hoy   = date.today()
    year  = hoy.year if mes >= hoy.month else hoy.year + 1
    expiry = _tercer_viernes(year, mes)

    return tipo, strike, expiry


def _tercer_viernes(year: int, month: int) -> date:
    """Tercer viernes del mes (vencimiento estándar ByMA)."""
    d = date(year, month, 1)
    # Weekday: 0=lun … 4=vie
    dias = (4 - d.weekday()) % 7      # días hasta el primer viernes
    return d.replace(day=1 + dias + 14)


# ─── Conversor raw API → OptionQuote ─────────────────────────────────────────

def _float_or_none(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _int_or_none(v) -> int | None:
    try:
        return int(v) if v not in (None, "", "?") else None
    except (TypeError, ValueError):
        return None


def _to_option_quote(raw: dict) -> "OptionQuote | None":
    """
    Convierte el dict crudo de la API IOL en un OptionQuote tipado.

    Estructura real del endpoint /Opciones:
      {
        "simbolo": "GFGC43487A",
        "tipoOpcion": "Call",            ← tipo directo
        "fechaVencimiento": "2026-04-17T00:00:00",
        "descripcion": "Call GGAL 4,348.70 Vencimiento: 17/04/2026",
        "cotizacion": {                  ← precios ANIDADOS aquí
          "ultimoPrecio": 2603.01,
          "puntas": null | [{...}],
          "volumenNominal": ...
        }
      }
    """
    simbolo = raw.get("simbolo") or raw.get("ticker") or raw.get("descripcionAbreviada", "")
    if not simbolo:
        return None

    # ── Tipo desde tipoOpcion ────────────────────────────────────────────
    tipo_raw = str(raw.get("tipoOpcion") or "").strip().lower()
    if tipo_raw in ("call", "c", "compra"):
        tipo: str | None = "CALL"
    elif tipo_raw in ("put", "v", "venta"):
        tipo = "PUT"
    else:
        tipo = None

    # ── Vencimiento desde fechaVencimiento ───────────────────────────────
    expiry: date | None = None
    vcto_raw = raw.get("fechaVencimiento") or raw.get("vencimiento")
    if vcto_raw:
        try:
            expiry = datetime.fromisoformat(str(vcto_raw)[:10]).date()
        except (ValueError, TypeError):
            pass

    # ── Strike desde descripcion ("Call GGAL 4,348.70 Vencimiento: ...") ─
    # El ticker codifica el strike sin decimales (43487), pero la descripción
    # lo trae correctamente formateado (4,348.70). Usamos descripcion como
    # fuente primaria y el ticker / 10 como fallback.
    strike: float | None = None
    desc = str(raw.get("descripcion") or "")
    if desc:
        parts = desc.split()
        # índice 2: "4,348.70" (Call GGAL {strike} Vencimiento: ...)
        if len(parts) >= 3:
            try:
                strike = float(parts[2].replace(",", ""))
            except ValueError:
                pass

    # ── Fallback completo: parsear desde el ticker ───────────────────────
    if tipo is None or strike is None or expiry is None:
        parsed = parse_ticker(str(simbolo))
        if parsed is None:
            logger.debug("Ticker no reconocido: %s — omitido.", simbolo)
            return None
        p_tipo, p_strike, p_expiry = parsed
        tipo   = tipo   or p_tipo
        # strike del ticker está desplazado 1 decimal (43487 → 4348.7)
        if strike is None:
            strike = p_strike / 10.0
        expiry = expiry or p_expiry

    # ── Precios desde cotizacion (ANIDADO) ───────────────────────────────
    cot = raw.get("cotizacion") or {}

    ultimo  = _float_or_none(cot.get("ultimoPrecio") or cot.get("ultimo"))
    volumen = _int_or_none(cot.get("volumenNominal") or cot.get("volumen"))

    bid = ask = None
    puntas = cot.get("puntas")
    if isinstance(puntas, list) and puntas:
        bid = _float_or_none(puntas[0].get("precioCompra"))
        ask = _float_or_none(puntas[0].get("precioVenta"))
    elif isinstance(puntas, dict):
        bid = _float_or_none(puntas.get("precioCompra"))
        ask = _float_or_none(puntas.get("precioVenta"))

    return OptionQuote(
        simbolo=str(simbolo),
        tipo=tipo,
        strike=strike,
        expiry=expiry,
        bid=bid,
        ask=ask,
        ultimo=ultimo,
        volumen=volumen,
    )


# ─── MarketDataFeed ──────────────────────────────────────────────────────────

SnapshotCallback = Callable[[MarketSnapshot], Awaitable[None]]


class MarketDataFeed:
    """
    Servicio de polling de mercado para la cadena de opciones GGAL.

    Uso típico
    ----------
    async with IOLClient(u, p) as client:
        feed = MarketDataFeed(client, interval=5.0)
        feed.on_snapshot(mi_handler)
        await feed.start()
        await asyncio.sleep(60)     # correr por 60 segundos
        await feed.stop()
    """

    def __init__(
        self,
        client:     IOLClient,
        mercado:    str   = MERCADO,
        subyacente: str   = SUBYACENTE,
        interval:   float = 5.0,
    ) -> None:
        self._client     = client
        self._mercado    = mercado
        self._subyacente = subyacente
        self._interval   = interval
        self._callbacks: list[SnapshotCallback] = []
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_snapshot: MarketSnapshot | None = None

    # ── API pública ──────────────────────────────────────────────────────

    def on_snapshot(self, callback: SnapshotCallback) -> None:
        """Registra un callback async que recibirá cada MarketSnapshot nuevo."""
        self._callbacks.append(callback)

    async def start(self) -> None:
        """Inicia el polling en segundo plano."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop(), name="market_data_feed")
        logger.info(
            "MarketDataFeed iniciado — %s/%s cada %.1f s.",
            self._mercado, self._subyacente, self._interval,
        )

    async def stop(self) -> None:
        """Detiene el polling limpiamente."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("MarketDataFeed detenido.")

    @property
    def last_snapshot(self) -> MarketSnapshot | None:
        return self._last_snapshot

    # ── Loop interno ─────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        while self._running:
            t0 = asyncio.get_event_loop().time()
            try:
                snapshot = await self._fetch_snapshot()
                self._last_snapshot = snapshot
                for cb in self._callbacks:
                    try:
                        await cb(snapshot)
                    except Exception as exc:
                        logger.error("Error en callback de snapshot: %s", exc)
            except IOLRequestError as exc:
                logger.warning("Error de API en snapshot: %s", exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Error inesperado en poll_loop: %s", exc)

            elapsed = asyncio.get_event_loop().time() - t0
            await asyncio.sleep(max(0.0, self._interval - elapsed))

    async def _fetch_snapshot(self) -> MarketSnapshot:
        """Obtiene spot + cadena de opciones en paralelo."""
        spot_task, opts_task = (
            asyncio.create_task(self._fetch_spot()),
            asyncio.create_task(self._fetch_options()),
        )
        spot, raw_opts = await asyncio.gather(spot_task, opts_task)

        opciones: list[OptionQuote] = []
        for raw in raw_opts:
            q = _to_option_quote(raw)
            if q is not None:
                opciones.append(q)

        logger.debug(
            "Snapshot: spot=%s | %d/%d opciones parseadas.",
            spot, len(opciones), len(raw_opts),
        )
        return MarketSnapshot(ts=datetime.now(), spot=spot, opciones=opciones)

    async def _fetch_spot(self) -> float | None:
        try:
            data = await self._client.get_quote(self._mercado, self._subyacente)
            return _float_or_none(data.get("ultimoPrecio") or data.get("ultimo"))
        except IOLRequestError as exc:
            logger.warning("No se pudo obtener spot %s: %s", self._subyacente, exc)
            return None

    async def _fetch_options(self) -> list[dict]:
        raw = await self._client.get_options_chain(self._mercado, self._subyacente)
        if isinstance(raw, dict):
            raw = raw.get("opciones") or raw.get("items") or [raw]
        return raw if isinstance(raw, list) else []
