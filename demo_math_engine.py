"""
Demo Módulo 3: obtiene un snapshot en vivo y muestra IV + griegas para GGAL.
Uso: python demo_math_engine.py
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from iol_client import IOLClient, IOLAuthError
from market_data import MarketDataFeed, MarketSnapshot
from math_engine import enrich_snapshot, DEFAULT_RISK_FREE_RATE, OptionPricing

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _tabla_greeks(pricings: list[OptionPricing], spot: float) -> None:
    # Filtrar solo opciones con IV calculada y ordenar por strike
    con_iv = sorted(
        [p for p in pricings if p.greeks.iv is not None],
        key=lambda p: (p.quote.expiry, p.quote.strike),
    )
    if not con_iv:
        print("  (ninguna opcion con IV calculada — mercado probablemente cerrado)")
        return

    print(f"\n  {'Ticker':<18} {'Tipo':<5} {'Strike':>8} {'Spot':>8} "
          f"{'Mid':>8} {'BSM':>8} {'IV%':>7} "
          f"{'Delta':>7} {'Gamma':>8} {'Theta':>8} {'Vega':>7} {'DTE':>5}")
    print("  " + "-" * 105)

    for p in con_iv:
        q  = p.quote
        g  = p.greeks
        mid = q.mid or q.ultimo or 0
        misp = p.mispricing
        misp_str = f"{misp:+.2f}" if misp is not None else "  N/D"
        print(
            f"  {q.simbolo:<18} {q.tipo:<5} {q.strike:>8.0f} {spot:>8.0f} "
            f"{mid:>8.2f} {g.price:>8.2f} {(g.iv or 0)*100:>6.1f}% "
            f"{g.delta:>7.4f} {g.gamma:>8.6f} {g.theta:>8.3f} {g.vega:>7.3f} "
            f"{q.dias_al_vencimiento:>5}d"
        )


async def main() -> None:
    load_dotenv()
    username = os.getenv("IOL_USERNAME")
    password = os.getenv("IOL_PASSWORD")
    if not username or not password:
        print("Faltan IOL_USERNAME / IOL_PASSWORD en .env")
        sys.exit(1)

    snapshot_ref: list[MarketSnapshot] = []

    async def capturar(snap: MarketSnapshot) -> None:
        snapshot_ref.append(snap)
        await feed.stop()

    async with IOLClient(username, password) as client:
        feed = MarketDataFeed(client, interval=5.0)
        feed.on_snapshot(capturar)
        await feed.start()
        while feed._running:
            await asyncio.sleep(0.2)

    if not snapshot_ref:
        print("No se recibio ningun snapshot.")
        return

    snap = snapshot_ref[0]
    print(f"\nSpot GGAL: {snap.spot}  |  {len(snap.opciones)} opciones  "
          f"|  Tasa r={DEFAULT_RISK_FREE_RATE*100:.0f}%\n")

    pricings = enrich_snapshot(snap, r=DEFAULT_RISK_FREE_RATE)

    # Agrupar por vencimiento
    for expiry in snap.expiries():
        grupo = [p for p in pricings if p.quote.expiry == expiry]
        print(f"{'='*105}")
        print(f"  Vencimiento: {expiry}  ({grupo[0].quote.dias_al_vencimiento}d)")
        print(f"{'='*105}")
        _tabla_greeks(grupo, snap.spot or 0)
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except IOLAuthError as e:
        print(f"Error de autenticacion: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
