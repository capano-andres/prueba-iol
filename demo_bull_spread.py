"""
Demo de BullCallSpreadStrategy con datos sintéticos.

Simula un snapshot de COME con la cadena de opciones en torno a $48.59
(precio documentado en estrategia-come.md, 11/04/2026).

Uso:
    python demo_bull_spread.py
"""

import asyncio
import logging
from datetime import date, datetime

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s: %(message)s")

from market_data import OptionQuote, MarketSnapshot
from math_engine import enrich_snapshot, DEFAULT_RISK_FREE_RATE
from strategy_bull_spread import BullCallSpreadStrategy, BullSpreadConfig, SpreadSignal

# ─── Parámetros de mercado sintéticos ────────────────────────────────────────

SPOT_COME = 48.59   # Precio documentado en estrategia-come.md

# Vencimientos típicos ByMA
EXPIRY_JUN = date(2026, 6, 19)   # 69 DTE ~
EXPIRY_MAY = date(2026, 5, 15)   # 34 DTE ~

# Cadena de opciones sintética (Calls sobre COME)
# Precios ficticios pero coherentes con IV ~80% sobre un spot de ~48.59
# En el mercado real, estos tickers serían tipo: COMEC48J, COMEC54J, etc.
SYNTHETIC_OPTIONS = [
    # ── Vencimiento Junio 2026 (DTE ≈ 69) ──────────────────────────────
    # Strike 44 — ITM
    {"simbolo": "COMEC44J", "tipoOpcion": "Call", "fechaVencimiento": str(EXPIRY_JUN),
     "descripcion": f"Call COME 44.00 Vencimiento: {EXPIRY_JUN}",
     "cotizacion": {"ultimoPrecio": 7.80, "puntas": [{"precioCompra": 7.20, "precioVenta": 8.40}]}},
    # Strike 48 — ATM
    {"simbolo": "COMEC48J", "tipoOpcion": "Call", "fechaVencimiento": str(EXPIRY_JUN),
     "descripcion": f"Call COME 48.00 Vencimiento: {EXPIRY_JUN}",
     "cotizacion": {"ultimoPrecio": 5.60, "puntas": [{"precioCompra": 5.20, "precioVenta": 6.00}]}},
    # Strike 50 — ligeramente OTM
    {"simbolo": "COMEC50J", "tipoOpcion": "Call", "fechaVencimiento": str(EXPIRY_JUN),
     "descripcion": f"Call COME 50.00 Vencimiento: {EXPIRY_JUN}",
     "cotizacion": {"ultimoPrecio": 4.80, "puntas": [{"precioCompra": 4.40, "precioVenta": 5.20}]}},
    # Strike 54 — OTM (candidato a short_leg)
    {"simbolo": "COMEC54J", "tipoOpcion": "Call", "fechaVencimiento": str(EXPIRY_JUN),
     "descripcion": f"Call COME 54.00 Vencimiento: {EXPIRY_JUN}",
     "cotizacion": {"ultimoPrecio": 3.10, "puntas": [{"precioCompra": 2.80, "precioVenta": 3.40}]}},
    # Strike 55 — OTM (candidato a short_leg)
    {"simbolo": "COMEC55J", "tipoOpcion": "Call", "fechaVencimiento": str(EXPIRY_JUN),
     "descripcion": f"Call COME 55.00 Vencimiento: {EXPIRY_JUN}",
     "cotizacion": {"ultimoPrecio": 2.80, "puntas": [{"precioCompra": 2.50, "precioVenta": 3.10}]}},
    # Strike 60 — deep OTM
    {"simbolo": "COMEC60J", "tipoOpcion": "Call", "fechaVencimiento": str(EXPIRY_JUN),
     "descripcion": f"Call COME 60.00 Vencimiento: {EXPIRY_JUN}",
     "cotizacion": {"ultimoPrecio": 1.40, "puntas": [{"precioCompra": 1.10, "precioVenta": 1.70}]}},

    # ── Vencimiento Mayo 2026 (DTE ≈ 34) ──────────────────────────────
    # Strike 48 — ATM
    {"simbolo": "COMEC48F", "tipoOpcion": "Call", "fechaVencimiento": str(EXPIRY_MAY),
     "descripcion": f"Call COME 48.00 Vencimiento: {EXPIRY_MAY}",
     "cotizacion": {"ultimoPrecio": 3.80, "puntas": [{"precioCompra": 3.50, "precioVenta": 4.10}]}},
    # Strike 54 — OTM corta
    {"simbolo": "COMEC54F", "tipoOpcion": "Call", "fechaVencimiento": str(EXPIRY_MAY),
     "descripcion": f"Call COME 54.00 Vencimiento: {EXPIRY_MAY}",
     "cotizacion": {"ultimoPrecio": 1.60, "puntas": [{"precioCompra": 1.30, "precioVenta": 1.90}]}},
]


def make_snapshot(spot: float, options_raw: list[dict]) -> MarketSnapshot:
    """Construye un MarketSnapshot sintético desde datos raw."""
    from market_data import _to_option_quote
    opciones = []
    for raw in options_raw:
        q = _to_option_quote(raw)
        if q:
            opciones.append(q)
    return MarketSnapshot(ts=datetime.now(), spot=spot, opciones=opciones)


def print_signals(signals: list[SpreadSignal]) -> None:
    print("\n" + "=" * 70)
    print(f"  SEÑALES BULL CALL SPREAD ({len(signals)} encontradas)")
    print("=" * 70)
    if not signals:
        print("  (ninguna — ajustar parámetros o esperar mejor precio)")
        return

    for i, sig in enumerate(signals, 1):
        lq = sig.long_leg.quote
        sq = sig.short_leg.quote
        print(f"\n  [{i}] Score: {sig.score:.2f}  |  DTE: {lq.dias_al_vencimiento}")
        print(f"       Long  → {lq.simbolo} (K={lq.strike:.1f})  bid={lq.bid:.2f} ask={lq.ask:.2f}")
        print(f"       Short → {sq.simbolo} (K={sq.strike:.1f})  bid={sq.bid:.2f} ask={sq.ask:.2f}")
        print(f"       Prima neta pagada : {sig.net_premium:.2f} ARS")
        print(f"       Ganancia máxima   : {sig.max_profit:.2f} ARS  ({sig.max_profit / SPOT_COME * 100:.1f}% del spot)")
        print(f"       Pérdida máxima    : {sig.max_loss:.2f} ARS   ({sig.max_loss / SPOT_COME * 100:.1f}% del spot)")
        print(f"       Breakeven en vto  : {sig.breakeven:.2f} ARS  (spot debe subir {(sig.breakeven - SPOT_COME) / SPOT_COME * 100:.1f}%)")
        print(f"       Reward/Risk       : {sig.reward_risk:.2f}x")
        print(f"       IV Long  : {sig.long_leg.greeks.iv:.1%}" if sig.long_leg.greeks.iv else "       IV Long  : N/D")
        print(f"       Δ Long   : {sig.long_leg.greeks.delta:.3f}")
        print(f"       Θ Long   : {sig.long_leg.greeks.theta:.4f} ARS/día")


async def main() -> None:
    print(f"\n  Spot COME: ${SPOT_COME:.2f} ARS")
    print(f"  Fecha: {date.today()}")

    snapshot = make_snapshot(SPOT_COME, SYNTHETIC_OPTIONS)
    pricings = enrich_snapshot(snapshot, r=DEFAULT_RISK_FREE_RATE)

    print(f"\n  Opciones parseadas: {len(snapshot.opciones)}")
    print(f"  Pricings enriquecidos (con griegas BSM): {len(pricings)}")
    print("\n  Cotizaciones recibidas:")
    for p in pricings:
        q = p.quote
        g = p.greeks
        print(
            f"    {q.simbolo:<14} K={q.strike:6.1f}  DTE={q.dias_al_vencimiento:3d}  "
            f"mid={q.mid or 0:6.2f}  IV={g.iv:.1%}" if g.iv else
            f"    {q.simbolo:<14} K={q.strike:6.1f}  DTE={q.dias_al_vencimiento:3d}  "
            f"mid={q.mid or 0:6.2f}  IV=N/D"
        )

    # ── Test 1: Config por defecto ─────────────────────────────────────────────
    print("\n\n" + "-" * 70)
    print("  TEST 1: Configuracion por defecto (ancho=12%, maxPrima=6%)")
    print("-" * 70)

    class FakeOMS:
        _positions = {}

    cfg = BullSpreadConfig()
    strategy = BullCallSpreadStrategy(FakeOMS(), cfg)  # type: ignore
    signals = strategy.evaluar(pricings, SPOT_COME)
    print_signals(signals)

    # ── Test 2: Config más agresiva ────────────────────────────────────────────
    print("\n\n" + "-" * 70)
    print("  TEST 2: Config agresiva (ancho=15%, maxPrima=10%, minR/R=1.2)")
    print("-" * 70)

    cfg2 = BullSpreadConfig(
        strike_width_pct=0.15,
        max_net_premium_pct=0.10,
        min_reward_risk_ratio=1.2,
    )
    strategy2 = BullCallSpreadStrategy(FakeOMS(), cfg2)  # type: ignore
    signals2 = strategy2.evaluar(pricings, SPOT_COME)
    print_signals(signals2)

    print("\n\n" + "=" * 70)
    print("  RESUMEN:")
    print(f"    Config default  → {len(signals)} señales")
    print(f"    Config agresiva → {len(signals2)} señales")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
