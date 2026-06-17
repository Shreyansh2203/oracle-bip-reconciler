import asyncio
import json
import os

import httpx


async def test_jsons():
    # Use relative path from the project root
    json_dir = 'data/JSON'
    url = 'http://localhost:8000/v1/reconcile/batch'

    if not os.path.exists(json_dir):
        print(f"Error: {json_dir} directory not found.")
        return

    files = [f for f in os.listdir(json_dir) if f.endswith('.json')]

    total_files = len(files)
    successful_requests = 0
    failed_requests = 0
    total_invoices_evaluated = 0
    total_invoices_matched = 0

    file_results = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        for file in files:
            file_path = os.path.join(json_dir, file)
            try:
                with open(file_path, encoding='utf-8') as f:
                    payload = json.load(f)
            except Exception as e:
                file_results.append((file, f"Error reading file: {e}"))
                continue

            try:
                response = await client.post(url, json=payload)
                if response.status_code != 200:
                    failed_requests += 1
                    file_results.append((file, f"Failed HTTP {response.status_code}: {response.text[:100]}"))
                    continue

                successful_requests += 1
                data = response.json()

                receipt_matched = bool(data.get('fusion_receipt_number'))
                invoices = data.get('invoices', [])
                matched_invoices = [inv for inv in invoices if inv.get('fusion_invoice_number')]

                total_invoices_evaluated += len(invoices)
                total_invoices_matched += len(matched_invoices)

                warnings = data.get('meta_data', {}).get('warnings', []) if data.get('meta_data') else []

                result_str = f"Receipt Matched: {'YES' if receipt_matched else 'NO'}. Invoices: {len(matched_invoices)}/{len(invoices)} matched."
                if warnings:
                    result_str += f" Warnings: {warnings[0]}..."

                file_results.append((file, result_str))

            except Exception as e:
                failed_requests += 1
                file_results.append((file, f"Request exception: {e}"))

    print("=== FINAL SUMMARY ===")
    print(f"Total Files Tested: {total_files}")
    print(f"Successful Requests (HTTP 200): {successful_requests}")
    print(f"Failed Requests (HTTP 400/500): {failed_requests}")
    print(f"Total Invoices Evaluated: {total_invoices_evaluated}")
    print(f"Total Invoices Matched: {total_invoices_matched}")
    print("=====================")
    for f, res in file_results:
        print(f"{f}: {res}")

if __name__ == '__main__':
    asyncio.run(test_jsons())
