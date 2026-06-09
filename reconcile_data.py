import csv
import json
import glob
import os

invoice_csv = r"c:\Users\Shreyansh Srivastava\Downloads\Invoice Details Report_Extract.csv"
receipt_csv = r"c:\Users\Shreyansh Srivastava\Downloads\Receipt Details Report_Extract.csv"
json_dir = r"c:\Users\Shreyansh Srivastava\OneDrive - acsesolutions.com\Desktop\Test\JSON"

print("Loading data from CSVs...")
receipt_numbers = set()
with open(receipt_csv, 'r', encoding='utf-8-sig', errors='ignore') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if 'RECEIPT_NUMBER' in row and row['RECEIPT_NUMBER']:
            receipt_numbers.add(row['RECEIPT_NUMBER'].strip())

transaction_numbers = set()
with open(invoice_csv, 'r', encoding='utf-8-sig', errors='ignore') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if 'TRANSACTION_NUMBER' in row and row['TRANSACTION_NUMBER']:
            transaction_numbers.add(row['TRANSACTION_NUMBER'].strip())

print(f"Loaded {len(receipt_numbers)} unique receipts and {len(transaction_numbers)} unique invoices.")

results = []
for json_file in glob.glob(os.path.join(json_dir, "*.json")):
    with open(json_file, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            continue
    
    filename = os.path.basename(json_file)
    receipt_num = str(data.get("payment_reference", "")).strip()
    
    receipt_match = receipt_num in receipt_numbers if receipt_num else False
        
    invoice_matches = []
    for inv in data.get("invoices", []):
        inv_num = str(inv.get("invoice_number", "")).strip()
        inv_match = inv_num in transaction_numbers if inv_num else False
        invoice_matches.append({"invoice_number": inv_num, "matched": inv_match})
        
    results.append({
        "file": filename,
        "receipt_number": receipt_num,
        "receipt_matched": receipt_match,
        "invoices": invoice_matches
    })

print("\n--- RECONCILIATION RESULTS ---")
for res in results:
    print(f"File: {res['file']}")
    print(f"  Receipt ({res['receipt_number']}): {'FOUND' if res['receipt_matched'] else 'NOT FOUND'}")
    for inv in res['invoices']:
        print(f"    Invoice ({inv['invoice_number']}): {'FOUND' if inv['matched'] else 'NOT FOUND'}")
    print("-" * 30)
