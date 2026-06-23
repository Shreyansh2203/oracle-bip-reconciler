import asyncio
import glob
import json
import os
import time

import httpx


async def test_localhost_jsons():
    json_dir = os.path.join("data", "JSON")
    files = glob.glob(os.path.join(json_dir, "*.json"))
    print(f"Found {len(files)} JSON files. Testing against http://127.0.0.1:8001/v1/reconcile/batch...\n")

    results = []

    async with httpx.AsyncClient(timeout=None) as client:
        for i, file in enumerate(files):
            print(f"[{i + 1}/{len(files)}] Processing {os.path.basename(file)}...")
            try:
                with open(file, encoding="utf-8") as f:
                    data = json.load(f)

                start = time.time()
                response = await client.post("http://127.0.0.1:8001/v1/reconcile/batch", json=data)
                dur = time.time() - start

                if response.status_code == 200:
                    res = response.json()
                    if res is None:
                        print(f"  -> No matches found | Time: {dur:.2f}s")
                        results.append((os.path.basename(file), 0, len(data.get("invoices", [])), 0, dur))
                        continue

                    # Count matches
                    invs = res.get("invoices", [])
                    inv_total = len(invs)
                    inv_matched = sum(1 for inv in invs if inv.get("fusion_invoice_number"))

                    # Receipt match
                    receipt_matched = 1 if res.get("fusion_receipt_number") else 0

                    res_str = (
                        f"Invoices: {inv_matched}/{inv_total} | Receipt Matched: {receipt_matched} | Time: {dur:.2f}s"
                    )
                    print(f"  -> {res_str}")
                    results.append((os.path.basename(file), inv_matched, inv_total, receipt_matched, dur))
                else:
                    print(f"  -> ERROR: API returned status {response.status_code} - {response.text}")
                    results.append((os.path.basename(file), "ERROR", response.status_code, "", 0))

            except Exception as e:
                print(f"  -> ERROR: {e}")
                results.append((os.path.basename(file), "ERROR", str(e), "", 0))

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for name, m, t, r, d in results:
        if m == "ERROR":
            print(f"{name:50} -> ERROR")
        else:
            print(f"{name:50} -> Invoices: {m:>3}/{t:<3} | Receipt: {r} | {d:.1f}s")

    success_count = sum(1 for n, m, t, r, d in results if m != "ERROR" and (m > 0 or r > 0))
    print("=" * 70)
    print(f"TOTAL: {success_count}/{len(results)} files had at least 1 successful match.")


if __name__ == "__main__":
    asyncio.run(test_localhost_jsons())
