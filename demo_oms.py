"""
Demo Módulo 4: integración completa M1+M2+M3+M4 en dry-run.
Simula un ciclo intradiario completo: abrir → analizar → cerrar.
Uso: python demo_oms.py
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from iol_client import IOLClient, IOLAuthError
from market_data import MarketDataFeed, MarketSnapshot
from math_engine import enrich_snapshot, DEFAULT_RISK_FREE_RATE
from oms import OMS, IOLProfile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("demo_oms")


async def main() -> None:
    load_dotenv()
    username = os.getenv("IOL_USERNAME")
    password = os.getenv("IOL_PASSWORD")
    if not username or not password:
        print("Faltan IOL_USERNAME / IOL_PASSWORD en .env")
        sys.exit(1)

    snap_ref: list[MarketSnapshot] = []

    async def capturar(snap: MarketSnapshot) -> None:
        snap_ref.append(snap)
        await feed.stop()

    # ── 1. Obtener snapshot ────────────────────────────────────────────────
    async with IOLClient(username, password) as client:
        feed = MarketDataFeed(client, interval=5.0)
        feed.on_snapshot(capturar)
        await feed.start()
        while feed._running:
            await asyncio.sleep(0.2)

        if not snap_ref:
            print("No se recibio snapshot.")
            return

        snap = snap_ref[0]
        print(f"\nSpot GGAL: {snap.spot}  |  {len(snap.opciones)} opciones\n")

        # ── 2. Motor matematico ─────────────────────────────────────────────
        pricings = enrich_snapshot(snap, r=DEFAULT_RISK_FREE_RATE)
        con_iv   = [p for p in pricings if p.greeks.iv is not None]
        print(f"Opciones con IV calculada: {len(con_iv)}")

        # ── 3. Seleccionar candidatos ATM (calls y puts mas cercanos al spot) ─
        if snap.spot is None:
            print("Spot no disponible.")
            return

        calls_atm = sorted(
            [p for p in con_iv if p.quote.tipo == "CALL"],
            key=lambda p: abs(p.quote.strike - snap.spot),
        )[:2]
        puts_atm = sorted(
            [p for p in con_iv if p.quote.tipo == "PUT"],
            key=lambda p: abs(p.quote.strike - snap.spot),
        )[:2]

        print("\n  Candidatos seleccionados:")
        for p in calls_atm + puts_atm:
            print(f"    {p.quote.simbolo:<18} {p.quote.tipo:<5} "
                  f"strike={p.quote.strike:.0f}  mid={p.quote.mid or 0:.2f}  "
                  f"IV={p.greeks.iv*100:.1f}%  delta={p.greeks.delta:.3f}")

        # ── 4. OMS dry-run ──────────────────────────────────────────────────
        oms = OMS(client, profile=IOLProfile.GOLD, dry_run=True)

        print("\n--- Abriendo posiciones (dry-run) ---")
        ordenes = []
        for p in calls_atm:
            mid = p.quote.mid or p.greeks.price
            if mid and mid > 0:
                order = await oms.open_position(
                    simbolo=p.quote.simbolo,
                    tipo=p.quote.tipo,
                    lado="LONG",
                    cantidad=1,
                    precio_limite=round(mid * 0.99, 2),  # limite un 1% bajo el mid
                )
                ordenes.append(order)

        for p in puts_atm:
            mid = p.quote.mid or p.greeks.price
            if mid and mid > 0:
                order = await oms.open_position(
                    simbolo=p.quote.simbolo,
                    tipo=p.quote.tipo,
                    lado="SHORT",
                    cantidad=1,
                    precio_limite=round(mid * 1.01, 2),  # limite un 1% sobre el mid
                )
                ordenes.append(order)

        # ── 5. Auditoría de riesgo ITM ──────────────────────────────────────
        print("\n--- Auditoria de riesgo ITM (DTE <= 1) ---")
        en_riesgo = oms.audit_itm_risk(pricings, snap.spot, dte_umbral=1)
        if en_riesgo:
            print(f"  ALERTA: {len(en_riesgo)} posicion(es) ITM con vencimiento inminente:")
            for pos in en_riesgo:
                print(f"    {pos.simbolo}  lado={pos.lado}")
        else:
            print("  Sin riesgo ITM inminente.")

        # ── 6. Reporte antes del cierre ─────────────────────────────────────
        print("\n" + oms.reporte(snap))

        # ── 7. Simular cierre intradiario ───────────────────────────────────
        print("\n--- Cerrando posiciones intradiarias (dry-run) ---")
        await oms.close_all_intraday(snap)

        # ── 8. Reporte final ────────────────────────────────────────────────
        print("\n" + oms.reporte(snap))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except IOLAuthError as e:
        print(f"Error de autenticacion: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
