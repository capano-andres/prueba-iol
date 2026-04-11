import asyncio
import os
from dotenv import load_dotenv
from api.models import ConfigureAIRequest
from engine import TradingEngine
from duckduckgo_search import DDGS

load_dotenv("C:/Users/capan/Desktop/Conexion IOL/.env")

async def test_deps():
    # Test DDGS
    print("Testing DDGS...")
    try:
        results = DDGS().news("GGAL merval", region='ar-es', max_results=5)
        print("DDGS results:", results)
    except Exception as e:
        print("DDGS Exception:", e)

    # Test IOL Client Get Quote
    print("\nTesting IOL Client...")
    engine = TradingEngine()
    try:
        await engine.initialize()
        quote = await engine._client.get_quote("bCBA", "GGAL")
        print("Quote GGAL:", quote)
    except Exception as e:
        print("IOL Exception:", e)
    finally:
        await engine.shutdown()

asyncio.run(test_deps())
