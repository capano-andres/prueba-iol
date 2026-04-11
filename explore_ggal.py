"""
Script de exploración: conecta a la API IOL v2 y muestra datos de GGAL.
Uso: python explore_ggal.py
"""

import asyncio
import json
import logging
import os
import sys

from dotenv import load_dotenv

from iol_client import IOLClient, IOLAuthError, IOLRequestError

# ------------------------------------------------------------------ #
# Configuración de logging
# ------------------------------------------------------------------ #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("explore_ggal")


# ------------------------------------------------------------------ #
# Helpers de presentación
# ------------------------------------------------------------------ #

def _seccion(titulo: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {titulo}")
    print("="*60)


def _mostrar_cotizacion(data: dict) -> None:
    _seccion("COTIZACIÓN GGAL — Bolsa de Buenos Aires")
    campos = [
        ("Último precio", "ultimoPrecio"),
        ("Variación %",   "variacion"),
        ("Apertura",      "apertura"),
        ("Máximo",        "maximo"),
        ("Mínimo",        "minimo"),
        ("Volumen",       "volumenNominal"),
        ("Bid (compra)",  "puntas", "precioCompra"),
        ("Ask (venta)",   "puntas", "precioVenta"),
    ]
    for campo in campos:
        if len(campo) == 2:
            label, key = campo
            valor = data.get(key, "N/D")
        else:
            label, key1, key2 = campo
            puntas = data.get(key1)
            valor = "N/D"
            if isinstance(puntas, list) and puntas:
                valor = puntas[0].get(key2, "N/D")
            elif isinstance(puntas, dict):
                valor = puntas.get(key2, "N/D")
        print(f"  {label:<20} {valor}")


def _mostrar_opciones(opciones: list) -> None:
    _seccion(f"CADENA DE OPCIONES GGAL — primeros 20 contratos")
    if not opciones:
        print("  (sin datos)")
        return

    header = f"{'Ticker':<18} {'Tipo':<5} {'Strike':>10} {'Vcto':<12} {'Bid':>10} {'Ask':>10}"
    print(f"  {header}")
    print(f"  {'-'*68}")

    for opt in opciones[:20]:
        # La estructura exacta depende del endpoint; intentamos varios campos
        ticker    = opt.get("simbolo") or opt.get("ticker") or opt.get("descripcionAbreviada", "?")
        tipo      = opt.get("tipoOpcion") or opt.get("tipo", "?")
        strike    = opt.get("strikePrice") or opt.get("precioEjercicio", "?")
        vcto      = opt.get("fechaVencimiento") or opt.get("vencimiento", "?")
        if vcto and len(str(vcto)) > 10:
            vcto = str(vcto)[:10]
        bid       = opt.get("precioCompra") or opt.get("bid", "?")
        ask       = opt.get("precioVenta") or opt.get("ask", "?")
        print(f"  {str(ticker):<18} {str(tipo):<5} {str(strike):>10} {str(vcto):<12} {str(bid):>10} {str(ask):>10}")


def _mostrar_portafolio(data: dict) -> None:
    _seccion("PORTAFOLIO — Cuenta bCBA")
    activos = data.get("activos") or data.get("titulos") or []
    if not activos:
        print("  Portafolio vacío o sin posiciones en bCBA.")
        return
    for item in activos:
        nombre  = item.get("titulo", {}).get("descripcion") or item.get("descripcion", "?")
        simbolo = item.get("titulo", {}).get("simbolo") or item.get("simbolo", "?")
        cant    = item.get("cantidad", "?")
        valorizado = item.get("valorizado") or item.get("valorioMercado", "?")
        print(f"  {simbolo:<12} {nombre:<30} Cant: {cant:<8} Val: {valorizado}")


def _mostrar_cuenta(data: dict) -> None:
    _seccion("ESTADO DE CUENTA")
    # Intentar distintas claves según la versión del endpoint
    saldo = (
        data.get("saldos")
        or data.get("cuentas")
        or [data]
    )
    if isinstance(saldo, list):
        for item in saldo:
            moneda = item.get("moneda") or item.get("divisa", "")
            disp   = item.get("saldo") or item.get("disponible", "?")
            print(f"  Moneda: {moneda:<6}  Disponible: {disp}")
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


# ------------------------------------------------------------------ #
# Función principal
# ------------------------------------------------------------------ #

async def main() -> None:
    load_dotenv()

    username = os.getenv("IOL_USERNAME")
    password = os.getenv("IOL_PASSWORD")

    if not username or not password:
        logger.error("Faltan IOL_USERNAME / IOL_PASSWORD en el archivo .env")
        sys.exit(1)

    logger.info("Conectando a la API de InvertirOnline…")

    try:
        async with IOLClient(username, password) as client:
            logger.info("Autenticación exitosa.")

            # 0. Perfil (siempre funciona)
            try:
                perfil = await client.get_profile()
                _seccion("PERFIL DE CUENTA")
                print(f"  Nombre:          {perfil.get('nombre')} {perfil.get('apellido')}")
                print(f"  N° Cuenta:       {perfil.get('numeroCuenta')}")
                print(f"  Perfil inversor: {perfil.get('perfilInversor')}")
                print(f"  TyC API activas: {'SI' if not perfil.get('actualizarTyC') else 'PENDIENTE'}")
                print(f"  TyC App:         {'SI' if not perfil.get('actualizarTyCApp') else 'PENDIENTE - aceptar en la app/web IOL'}")
            except IOLRequestError as e:
                logger.error("Error al obtener perfil: %s", e)

            # 1. Cotización GGAL
            try:
                cotizacion = await client.get_quote("bCBA", "GGAL")
                _mostrar_cotizacion(cotizacion)
            except IOLRequestError as e:
                logger.error("Error al obtener cotización GGAL: %s", e)
                print(f"\n  [!] No se pudo obtener la cotización: {e}")

            # 2. Cadena de opciones GGAL
            try:
                opciones = await client.get_options_chain("bCBA", "GGAL")
                if isinstance(opciones, dict):
                    opciones = opciones.get("opciones") or opciones.get("items") or [opciones]
                _mostrar_opciones(opciones)
            except IOLRequestError as e:
                logger.error("Error al obtener opciones GGAL: %s", e)
                print(f"\n  [!] Opciones no disponibles: {e}")

            # 3. Portafolio
            try:
                portafolio = await client.get_portfolio("argentina")
                _mostrar_portafolio(portafolio)
            except IOLRequestError as e:
                logger.error("Error al obtener portafolio: %s", e)
                print(f"\n  [!] Portafolio no disponible: {e}")

            # 4. Estado de cuenta
            try:
                cuenta = await client.get_account_state()
                _mostrar_cuenta(cuenta)
            except IOLRequestError as e:
                logger.error("Error al obtener estado de cuenta: %s", e)
                print(f"\n  [!] Estado de cuenta no disponible: {e}")

            print("\n")
            logger.info("Exploración completada.")

    except IOLAuthError as e:
        logger.error("Error de autenticación: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
