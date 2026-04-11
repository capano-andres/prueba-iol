"""
Módulo 3: Motor Matemático
- Black-Scholes-Merton (BSM) para opciones estilo europeo
- Griegas de primer orden: Delta, Gamma, Theta, Vega, Rho
- Volatilidad Implícita: Newton-Raphson + bisección como fallback
- Dividendos discretos (ByMA Clearing 2026 — sin ajuste de strikes desde jul-2026)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from market_data import MarketSnapshot, OptionQuote

logger = logging.getLogger(__name__)

# ─── Constantes ───────────────────────────────────────────────────────────────

_SQRT2   = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)

# Tasa libre de riesgo default en ARS.
# Actualizar con la tasa de cauciones bursátiles vigente (BADLAR, TNA, etc.).
DEFAULT_RISK_FREE_RATE = 0.45   # 45% anual continua

# Volatilidad implícita inicial para Newton-Raphson (rango típico GGAL: 60-120%)
_IV_INITIAL_GUESS = 0.80

# Límites y tolerancias para el solver de IV
_IV_MAX_ITER_NR = 100
_IV_TOLERANCE   = 1e-6
_IV_SIGMA_MIN   = 1e-6
_IV_SIGMA_MAX   = 20.0    # 2000 % como cota superior absoluta
_IV_MIN_VEGA    = 1e-10   # vega mínima antes de caer en bisección

# Precio mínimo de opción para calcular IV (debajo de esto se considera ruido)
MIN_OPTION_PRICE = 0.01

DIAS_ANIO = 365.0


# ─── Distribución normal estándar (sin dependencias externas) ─────────────────

def _N(x: float) -> float:
    """Función de distribución acumulada (CDF) normal estándar."""
    return 0.5 * math.erfc(-x / _SQRT2)


def _n(x: float) -> float:
    """Función de densidad de probabilidad (PDF) normal estándar."""
    return math.exp(-0.5 * x * x) / _SQRT2PI


# ─── Dataclasses de resultado ─────────────────────────────────────────────────

@dataclass(slots=True)
class GreeksResult:
    """Precio teórico BSM + griegas de primer orden."""
    price: float    # precio teórico (ARS)
    delta: float    # Δ — var. precio por cada ARS de movimiento del spot
    gamma: float    # Γ — var. de delta por cada ARS de movimiento del spot
    theta: float    # Θ — decaimiento temporal en ARS por día calendario
    vega:  float    # ν — var. precio por cada 1% de cambio en σ
    rho:   float    # ρ — var. precio por cada 1% de cambio en r
    iv:    float | None  # volatilidad implícita (ej: 0.85 = 85% anual)


@dataclass
class OptionPricing:
    """Par (cotización de mercado, valuación teórica) de una opción."""
    quote:  OptionQuote
    greeks: GreeksResult

    @property
    def mispricing(self) -> float | None:
        """
        mid_mercado − precio_BSM.
        Positivo → mercado sobrevalora la opción.
        Negativo → mercado subvalora la opción.
        """
        mid = self.quote.mid
        if mid is None or self.greeks.iv is None:
            return None
        return mid - self.greeks.price


# ─── Black-Scholes-Merton ──────────────────────────────────────────────────────

def bsm_price(
    tipo:  str,         # "CALL" | "PUT"
    S:     float,       # precio spot (ajustado por dividendos si corresponde)
    K:     float,       # strike
    T:     float,       # tiempo hasta vencimiento en años
    r:     float,       # tasa libre de riesgo anual continua
    sigma: float,       # volatilidad anual
    q:     float = 0.0, # tasa de dividendo continua anual
) -> float:
    """
    Precio teórico BSM para una opción europea.
    Cuando T ≤ 0 retorna el valor intrínseco.
    """
    if T <= 0.0 or sigma <= 0.0:
        if tipo == "CALL":
            return max(S - K, 0.0)
        return max(K - S, 0.0)

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    if tipo == "CALL":
        return S * math.exp(-q * T) * _N(d1) - K * math.exp(-r * T) * _N(d2)
    return K * math.exp(-r * T) * _N(-d2) - S * math.exp(-q * T) * _N(-d1)


def bsm_greeks(
    tipo:  str,
    S:     float,
    K:     float,
    T:     float,
    r:     float,
    sigma: float,
    q:     float = 0.0,
) -> GreeksResult:
    """
    Precio teórico + griegas completas de primer orden.
    Si T ≤ 0 o sigma ≤ 0 devuelve griegas en cero.
    """
    price = bsm_price(tipo, S, K, T, r, sigma, q)

    if T <= 0.0 or sigma <= 0.0:
        return GreeksResult(price=price, delta=0.0, gamma=0.0,
                            theta=0.0, vega=0.0, rho=0.0, iv=None)

    sqrt_T  = math.sqrt(T)
    d1      = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2      = d1 - sigma * sqrt_T
    exp_qT  = math.exp(-q * T)
    exp_rT  = math.exp(-r * T)
    nd1     = _n(d1)

    # ── Delta ────────────────────────────────────────────────────────────
    delta = exp_qT * _N(d1) if tipo == "CALL" else -exp_qT * _N(-d1)

    # ── Gamma (simétrico para call y put) ────────────────────────────────
    gamma = exp_qT * nd1 / (S * sigma * sqrt_T)

    # ── Theta (por día calendario) ───────────────────────────────────────
    base_theta = -S * sigma * exp_qT * nd1 / (2.0 * sqrt_T)
    if tipo == "CALL":
        theta = (base_theta - r * K * exp_rT * _N(d2)  + q * S * exp_qT * _N(d1))  / DIAS_ANIO
    else:
        theta = (base_theta + r * K * exp_rT * _N(-d2) - q * S * exp_qT * _N(-d1)) / DIAS_ANIO

    # ── Vega (por 1% de cambio en σ) ─────────────────────────────────────
    vega = S * exp_qT * nd1 * sqrt_T / 100.0

    # ── Rho (por 1% de cambio en r) ──────────────────────────────────────
    if tipo == "CALL":
        rho = K * T * exp_rT * _N(d2)  / 100.0
    else:
        rho = -K * T * exp_rT * _N(-d2) / 100.0

    return GreeksResult(price=price, delta=delta, gamma=gamma,
                        theta=theta, vega=vega, rho=rho, iv=None)


# ─── Volatilidad Implícita ─────────────────────────────────────────────────────

def implied_vol(
    tipo:          str,
    market_price:  float,
    S:             float,
    K:             float,
    T:             float,
    r:             float,
    q:             float = 0.0,
    initial_guess: float = _IV_INITIAL_GUESS,
) -> float | None:
    """
    Volatilidad implícita via Newton-Raphson con fallback a bisección.

    Retorna None si:
      - T ≤ 0 o precio de mercado ≤ MIN_OPTION_PRICE
      - El precio está fuera de los límites teóricos BSM
    """
    if T <= 0.0 or market_price <= MIN_OPTION_PRICE:
        return None

    # Validar que el precio esté dentro de los límites BSM
    exp_qT = math.exp(-q * T)
    exp_rT = math.exp(-r * T)
    if tipo == "CALL":
        lb = max(S * exp_qT - K * exp_rT, 0.0)
        ub = S * exp_qT
    else:
        lb = max(K * exp_rT - S * exp_qT, 0.0)
        ub = K * exp_rT

    if market_price <= lb or market_price >= ub:
        return None

    # ── Newton-Raphson ────────────────────────────────────────────────────
    sigma = initial_guess
    for _ in range(_IV_MAX_ITER_NR):
        price = bsm_price(tipo, S, K, T, r, sigma, q)
        diff  = price - market_price

        if abs(diff) < _IV_TOLERANCE:
            return sigma

        sqrt_T  = math.sqrt(T)
        d1      = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
        vega_raw = S * math.exp(-q * T) * _n(d1) * sqrt_T  # vega sin el /100

        if vega_raw < _IV_MIN_VEGA:
            break   # degenerado → bisección

        sigma -= diff / vega_raw
        sigma  = max(_IV_SIGMA_MIN, min(sigma, _IV_SIGMA_MAX))

    # ── Bisección fallback ────────────────────────────────────────────────
    return _iv_bisection(tipo, market_price, S, K, T, r, q)


def _iv_bisection(
    tipo: str, market_price: float,
    S: float, K: float, T: float, r: float, q: float,
) -> float | None:
    lo, hi = _IV_SIGMA_MIN, _IV_SIGMA_MAX
    for _ in range(300):
        mid   = (lo + hi) * 0.5
        price = bsm_price(tipo, S, K, T, r, mid, q)
        diff  = price - market_price
        if abs(diff) < _IV_TOLERANCE:
            return mid
        if diff > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) * 0.5


# ─── Dividendos discretos ─────────────────────────────────────────────────────

Dividend = tuple[date, float]   # (fecha_ex_dividendo, monto_en_ARS_por_acción)


def adjust_spot_for_dividends(
    S:         float,
    r:         float,
    dividends: Sequence[Dividend],
    hoy:       date,
    expiry:    date,
) -> float:
    """
    Devuelve S ajustado restando el valor presente de los dividendos discretos
    que caen entre hoy y el vencimiento.

    Contexto ByMA 2026
    ------------------
    Desde las series con vencimiento en julio 2026, ByMA ya NO ajusta
    el strike por dividendos ordinarios. Por eso debemos ajustar el spot
    en el modelo para no sobrevaluar calls ni subvaluar puts cerca de la
    fecha de corte.
    """
    pv_div = 0.0
    for ex_date, monto in dividends:
        if hoy < ex_date <= expiry:
            t_div  = (ex_date - hoy).days / DIAS_ANIO
            pv_div += monto * math.exp(-r * t_div)
    return max(S - pv_div, 1e-6)


# ─── Enriquecimiento del snapshot ─────────────────────────────────────────────

def enrich_snapshot(
    snapshot:  MarketSnapshot,
    r:         float = DEFAULT_RISK_FREE_RATE,
    dividends: Sequence[Dividend] | None = None,
    q:         float = 0.0,
) -> list[OptionPricing]:
    """
    Calcula IV y griegas para todas las opciones del snapshot.

    Lógica
    ------
    - Precio de mercado para IV: mid (bid+ask)/2, fallback a último precio.
    - Si no hay precio de mercado, se incluye la opción con iv=None y
      griegas calculadas con σ de referencia (_IV_INITIAL_GUESS).
    - Opciones con T < 0 (vencidas) se omiten.

    Parámetros
    ----------
    snapshot  : MarketSnapshot del Módulo 2
    r         : tasa libre de riesgo anual continua (default DEFAULT_RISK_FREE_RATE)
    dividends : lista de (fecha_ex_div, monto_ARS) — ver adjust_spot_for_dividends
    q         : tasa de dividendo continua (alternativa a dividends discretos)
    """
    if snapshot.spot is None or snapshot.spot <= 0:
        logger.warning("Snapshot sin spot válido — no se calculan griegas.")
        return []

    hoy    = snapshot.ts.date()
    result: list[OptionPricing] = []

    for opt in snapshot.opciones:
        T = (opt.expiry - hoy).days / DIAS_ANIO
        if T < 0:
            continue    # ya expiró

        # Spot ajustado por dividendos discretos (si corresponde)
        S = (
            adjust_spot_for_dividends(snapshot.spot, r, dividends, hoy, opt.expiry)
            if dividends
            else snapshot.spot
        )

        # Precio de mercado: mid > último > None
        market_price = opt.mid or opt.ultimo

        # Calcular IV
        iv = (
            implied_vol(opt.tipo, market_price, S, opt.strike, T, r, q)
            if market_price and market_price > MIN_OPTION_PRICE and T > 0
            else None
        )

        # Griegas: con IV real si existe, de lo contrario con σ de referencia
        sigma  = iv if iv is not None else _IV_INITIAL_GUESS
        greeks = bsm_greeks(opt.tipo, S, opt.strike, T, r, sigma, q)
        greeks.iv = iv

        result.append(OptionPricing(quote=opt, greeks=greeks))

    return result
