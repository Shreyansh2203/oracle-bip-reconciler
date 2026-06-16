import json
import os
import sys
import time

import httpx

API_KEY = os.getenv("API_KEY")
if not API_KEY:
    print("Error: API_KEY environment variable is required.")
    sys.exit(1)
URL_ASYNC = "http://127.0.0.1:8000/v2/reconcile/async"
URL_STATUS = "http://127.0.0.1:8000/v2/reconcile/status"
HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

def main():
    if len(sys.argv) < 2:
        print("Usage: python script_test_async.py <path_to_json>")
        sys.exit(1)

    payload_path = sys.argv[1]
    with open(payload_path, encoding="utf-8") as f:
        payload = json.load(f)

    print(f"Submitting payload with {len(payload.get('invoices', []))} invoices...")

    with httpx.Client() as client:
        # 1. Submit the job
        start_time = time.time()
        resp = client.post(URL_ASYNC, json=payload, headers=HEADERS, timeout=10.0)

        if resp.status_code != 200:
            print(f"Failed to submit: {resp.status_code} - {resp.text}")
            sys.exit(1)

        data = resp.json()
        job_id = data.get("job_id")

        print(f"[SUCCESS] Job submitted successfully in {time.time() - start_time:.3f}s")
        print(f"Job ID: {job_id}")
        print("Polling for completion...")

        # 2. Poll for completion
        while True:
            time.sleep(2)
            status_resp = client.get(f"{URL_STATUS}/{job_id}", headers=HEADERS)

            if status_resp.status_code != 200:
                print(f"Failed to get status: {status_resp.status_code} - {status_resp.text}")
                sys.exit(1)

            status_data = status_resp.json()
            status = status_data.get("status")

            if status == "completed":
                print(f"[SUCCESS] Job completed! Took {status_data['completed_at'] - status_data['created_at']:.3f}s")
                # print a summary of the result
                res = status_data.get("result", {})
                print(f"Matched invoices returned: {len(res.get('invoices', []))}")
                break
            elif status == "failed":
                print(f"[FAILED] Job failed: {status_data.get('error')}")
                break
            else:
                print(f"Job is {status}...")

if __name__ == "__main__":
    main()
