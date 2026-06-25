import httpx, json

payload = json.load(open("data/JSON/22.json"))
resp = httpx.Client().post("http://127.0.0.1:8001/v1/reconcile/batch", json=payload, timeout=None)
result = resp.json()
print("fusion_receipt_number:", result.get("fusion_receipt_number"))
print("fusion_receipt_date:", result.get("fusion_receipt_date"))
print("fusion_applied_amount:", result.get("fusion_applied_amount"))

invoices = result.get("invoices", [])
matched = [i for i in invoices if i.get("match_phase") == "MATCHED"]
print(f"Matched: {len(matched)}/{len(invoices)}")
