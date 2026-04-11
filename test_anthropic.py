import os
import asyncio
import aiohttp
from dotenv import load_dotenv

load_dotenv("C:/Users/capan/Desktop/Conexion IOL/.env")

async def test_anthropic():
    api_key = os.getenv("CLAUDE_API_KEY")
    
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 4096,
        "thinking": {
            "type": "enabled",
            "budget_tokens": 2048
        },
        "messages": [
            {"role": "user", "content": "Haz una busqueda profunda en tu mente, 1+1=?"}
        ]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload) as resp:
            print(resp.status)
            print(await resp.text())

asyncio.run(test_anthropic())
