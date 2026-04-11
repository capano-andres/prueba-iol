"""Script de un solo uso: imprime los tickers crudos de la cadena de opciones GGAL."""
import asyncio, os, json
from dotenv import load_dotenv
from iol_client import IOLClient

async def main():
    load_dotenv()
    async with IOLClient(os.getenv("IOL_USERNAME"), os.getenv("IOL_PASSWORD")) as c:
        raw = await c.get_options_chain("bCBA", "GGAL")
        if isinstance(raw, dict):
            raw = raw.get("opciones") or raw.get("items") or [raw]
        print(f"Total: {len(raw)} items\n")
        for item in raw[:10]:
            print(json.dumps(item, ensure_ascii=False, indent=2))
            print("---")

asyncio.run(main())
