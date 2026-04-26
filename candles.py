"""
Agregador de velas OHLC a partir de ticks de precio.

Diseñado para estrategias que requieren análisis técnico estándar
sobre timeframes intradiarios (5m, 10m, 15m), construyendo las velas
en memoria desde el polling del spot — IOL no expone OHLC intradiario.

Buckets alineados a la hora del reloj. Ejemplo con timeframe=5m:
  11:03:42 → bucket 11:00:00
  11:07:15 → bucket 11:05:00
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

_TZ_ARG = timezone(timedelta(hours=-3))


@dataclass
class Candle:
    bucket_ts: datetime
    open:  float
    high:  float
    low:   float
    close: float
    n_ticks: int


class CandleAggregator:
    """Agrega ticks en velas OHLC. Solo se "cierra" una vela cuando llega un tick del siguiente bucket."""

    def __init__(self, timeframe_min: int = 5, max_history: int = 500) -> None:
        self._tf = max(1, int(timeframe_min))
        self._max = max_history
        self._current: Candle | None = None
        self._closed: list[Candle] = []

    def _bucket_start(self, ts: datetime) -> datetime:
        floored = (ts.minute // self._tf) * self._tf
        return ts.replace(minute=floored, second=0, microsecond=0)

    def add_tick(self, price: float) -> Candle | None:
        """Agrega un tick. Si se cierra una vela (cambio de bucket), la retorna; si no, None."""
        now = datetime.now(tz=_TZ_ARG)
        bucket = self._bucket_start(now)

        if self._current is None:
            self._current = Candle(bucket, price, price, price, price, 1)
            return None

        if bucket == self._current.bucket_ts:
            self._current.close = price
            if price > self._current.high: self._current.high = price
            if price < self._current.low:  self._current.low  = price
            self._current.n_ticks += 1
            return None

        # Cambio de bucket: cerrar la anterior, abrir nueva
        closed = self._current
        self._closed.append(closed)
        if len(self._closed) > self._max:
            self._closed = self._closed[-self._max:]
        self._current = Candle(bucket, price, price, price, price, 1)
        return closed

    @property
    def closes(self) -> list[float]:
        """Cierres de velas YA CERRADAS (excluye la actual en construcción)."""
        return [c.close for c in self._closed]

    @property
    def n_closed(self) -> int:
        return len(self._closed)

    @property
    def current(self) -> Candle | None:
        return self._current

    @property
    def timeframe_min(self) -> int:
        return self._tf

    def reset(self) -> None:
        self._current = None
        self._closed = []
