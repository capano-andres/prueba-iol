"""
Demo Módulo 2: corre 3 snapshots de la cadena GGAL y muestra un resumen.
Uso: python demo_market_data.py
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from iol_client import IOLClient, IOLAuthError
from market_data import MarketDataFeed, MarketSnapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


async def handler(snap: MarketSnapshot) -> None:
    print(snap.resumen())

    if not snap.opciones:
        print("  (cadena de opciones vacía)")
        return

    # Tabla de los 5 calls más cercanos al spot
    calls_atm = sorted(
        [c for c in snap.calls() if c.mid is not None],
        key=lambda o: abs(o.strike - (snap.spot or 0)),
    )[:5]

    if calls_atm:
        print(f"\n  {'Ticker':<18} {'Tipo':<5} {'Strike':>10} {'Vcto':<12}"
              f" {'Bid':>8} {'Ask':>8} {'Mid':>8} {'Spread%':>8} {'DTE':>5}")
        print("  " + "-" * 82)
        for o in calls_atm:
            sp = f"{o.spread_pct * 100:.1f}%" if o.spread_pct is not None else "N/D"
            print(
                f"  {o.simbolo:<18} {o.tipo:<5} {o.strike:>10.0f} {str(o.expiry):<12}"
                f" {(o.bid or 0):>8.2f} {(o.ask or 0):>8.2f}"
                f" {(o.mid or 0):>8.2f} {sp:>8} {o.dias_al_vencimiento:>5}d"
            )
    print()


async def main() -> None:
    load_dotenv()
    username = os.getenv("IOL_USERNAME")
    password = os.getenv("IOL_PASSWORD")
    if not username or not password:
        print("Faltan IOL_USERNAME / IOL_PASSWORD en .env")
        sys.exit(1)

    n_snaps = 3

    async with IOLClient(username, password) as client:
        feed = MarketDataFeed(client, interval=6.0)
        feed.on_snapshot(handler)

        contador = 0

        async def contar(_snap: MarketSnapshot) -> None:
            nonlocal contador
            contador += 1
            if contador >= n_snaps:
                await feed.stop()

        feed.on_snapshot(contar)
        await feed.start()

        # Esperar a que el feed se detenga solo (tras n_snaps)
        while feed._running:
            await asyncio.sleep(0.2)

    print(f"OK: {n_snaps} snapshots completados.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except IOLAuthError as e:
        print(f"Error de autenticación: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
