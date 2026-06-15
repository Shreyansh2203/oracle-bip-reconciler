import asyncio
import json
import os

import httpx


async def test_single_file(client, url, json_dir, file):
    file_path = os.path.join(json_dir, file)
    try:
        with open(file_path, encoding="utf-8") as f:
            payload = json.load(f)

        response = await client.post(url, json=payload, headers={"X-API-Key": "123"})
        resp_json = response.json()

        if "error" in resp_json:
            result = f"Error: {resp_json['error']}"
        elif "detail" in resp_json:
            result = f"Detail: {resp_json['detail']}"
        else:
            matched = sum(1 for inv in resp_json.get("invoices", []) if inv.get("status") == "Matched")
            unmatched = sum(1 for inv in resp_json.get("invoices", []) if inv.get("status") == "Unmatched")
            result = f"Matched: {matched}, Unmatched: {unmatched}"

        print(f"[{file}] Status: {response.status_code} | {result}", flush=True)
    except Exception as e:
        print(f"[{file}] Failed! Exception: {e}", flush=True)

async def test_json_payloads():
    url = "https://urban-octo-tribble-rouge.vercel.app/v1/reconcile"
    json_dir = "JSON"

    files = [f for f in os.listdir(json_dir) if f.endswith(".json")]
    print(f"Testing {len(files)} JSON files concurrently against {url}...", flush=True)

    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = [test_single_file(client, url, json_dir, file) for file in files]
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(test_json_payloads())
