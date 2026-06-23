import asyncio
import glob
import json
import os
import time

from dotenv import load_dotenv

from src.main import reconcile_data_batch
from src.models import ReconciliationRequest

load_dotenv()


async def test_jsons():
    json_dir = os.path.join("data", "JSON")

    if not os.path.exists(json_dir):
        print(f"Error: {json_dir} directory not found.")
        return

    files = glob.glob(os.path.join(json_dir, "*.json"))
    print(f"Found {len(files)} JSON files. Testing...\n")

    results = []

    for i, file in enumerate(files):
        print(f"[{i + 1}/{len(files)}] Processing {os.path.basename(file)}...")
        try:
            with open(file, encoding="utf-8") as f:
                data = json.load(f)

            req = ReconciliationRequest(**data)

            start = time.time()
            res = await reconcile_data_batch(req)
            dur = time.time() - start

            if res is None:
                print(f"  -> No matches found | Time: {dur:.2f}s")
                results.append((os.path.basename(file), 0, len(req.invoices or []), 0, dur))
                continue

            # Count matches
            inv_total = len(res.invoices or [])
            inv_matched = sum(1 for inv in (res.invoices or []) if inv.fusion_invoice_number)

            # Receipt match
            receipt_matched = 1 if res.fusion_receipt_number else 0

            res_str = f"Invoices: {inv_matched}/{inv_total} | Receipt Matched: {receipt_matched} | Time: {dur:.2f}s"
            print(f"  -> {res_str}")

            results.append((os.path.basename(file), inv_matched, inv_total, receipt_matched, dur))

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

    # Calculate totals
    success_count = sum(1 for n, m, t, r, d in results if m != "ERROR" and (m > 0 or r > 0))
    print("=" * 70)
    print(f"TOTAL: {success_count}/{len(results)} files had at least 1 successful match.")


if __name__ == "__main__":
    asyncio.run(test_jsons())
