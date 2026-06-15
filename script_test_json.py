import asyncio
import json
import os
import traceback
from pathlib import Path

import httpx

API_URL = os.getenv("API_URL", "http://localhost:8000/v1/reconcile")
API_KEY = os.getenv("API_KEY", "test_key")

async def test_failed_json():
    target_file = Path("JSON/instance-VvPxgVm7EfGJRzuJt-Qyvg-payload-tracking-data_debug_Wkc7GVm7EfGKgvVsLjEvQw.json")

    print(f"Testing {target_file.name}...")

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            with open(target_file, encoding='utf-8') as f:
                data = json.load(f)

            response = await client.post(
                API_URL,
                json=data,
                headers={"X-API-Key": API_KEY}
            )

            print(f"Status Code: {response.status_code}")
            print(f"Response: {response.text[:500]}")
        except Exception as e:
            print(f"EXCEPTION TYPE: {type(e).__name__}")
            print(f"EXCEPTION MESSAGE: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_failed_json())
