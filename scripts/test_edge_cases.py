import asyncio
import glob
import json
import os

import httpx
from httpx import ReadTimeout

API_URL = "http://127.0.0.1:8000/v1/reconcile/batch"
TEST_DIR = "Real Test Cases"
RESULTS_FILE = os.path.join(TEST_DIR, "test_results.txt")


async def test_file(client: httpx.AsyncClient, filepath: str) -> str:
    filename = os.path.basename(filepath)
    try:
        with open(filepath, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        return f"{filename} - ERROR reading file: {e}"

    try:
        # Some edge cases like 10k invoices might take quite some time
        response = await client.post(API_URL, json=payload, timeout=300.0)
        status = response.status_code

        if status == 200:
            res_json = response.json()
            if res_json is None:
                return f"{filename} - PASS (Status: 200) - Result: null (No matches found)"
            else:
                match_phase = res_json.get("match_phase", "UNKNOWN")
                return f"{filename} - PASS (Status: 200) - Match Phase: {match_phase}"
        elif status == 422:
            return f"{filename} - PASS (Status: 422) - Validation Error (Expected for schema violations)"
        else:
            return f"{filename} - FAIL (Status: {status}) - {response.text}"
    except ReadTimeout:
        return f"{filename} - FAIL - ReadTimeout (Took >300s)"
    except Exception as e:
        return f"{filename} - FAIL - Exception: {e}"


async def main():
    json_files = sorted(glob.glob(os.path.join(TEST_DIR, "*.json")))
    if not json_files:
        print(f"No JSON files found in {TEST_DIR}")
        return

    print(f"Found {len(json_files)} test cases. Starting tests...")

    results = []
    # Using a long timeout for the overall client just in case
    async with httpx.AsyncClient(timeout=300.0) as client:
        # We test them sequentially to avoid overwhelming the local server or Oracle instance
        for i, filepath in enumerate(json_files, 1):
            filename = os.path.basename(filepath)
            print(f"[{i}/{len(json_files)}] Testing {filename} ...")
            res_str = await test_file(client, filepath)
            print(f"  -> {res_str}")
            results.append(res_str)

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        f.write("Edge Case Test Results:\n")
        f.write("=" * 50 + "\n")
        for res in results:
            f.write(res + "\n")

    print(f"Finished testing all edge cases. Results saved to {RESULTS_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
