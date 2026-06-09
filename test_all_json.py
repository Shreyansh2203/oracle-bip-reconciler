import os
import glob
import requests
import json
import time

json_dir = r"c:\Users\Shreyansh Srivastava\OneDrive - acsesolutions.com\Desktop\Test\JSON"
url = "http://127.0.0.1:8000/reconcile"

files = glob.glob(os.path.join(json_dir, "*.json"))

print(f"Found {len(files)} JSON files. Starting tests against {url}...\n")

total_start = time.time()

for f in files:
    filename = os.path.basename(f)
    print(f"--- {filename} ---")
    try:
        with open(f, 'r', encoding='utf-8') as file:
            payload = json.load(file)
            
        res = requests.post(url, json=payload)
        if res.status_code == 200:
            data = res.json()
            print(f"   Success! Evaluated {data.get('invoices_checked')} invoices in {data.get('execution_time_seconds')} seconds.")
        else:
            print(f"   Error HTTP {res.status_code}: {res.text}")
    except Exception as e:
        print(f"   Failed: {e}")
        
print(f"\nCompleted testing all {len(files)} files in {round(time.time() - total_start, 2)} seconds total.")
