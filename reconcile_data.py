import csv
import json
import glob
import os
from datetime import datetime
from typing import Optional

invoice_csv = r"c:\Users\Shreyansh Srivastava\Downloads\Invoice Details Report_Extract.csv"
receipt_csv = r"c:\Users\Shreyansh Srivastava\Downloads\Receipt Details Report_Extract.csv"
json_dir = r"c:\Users\Shreyansh Srivastava\OneDrive - acsesolutions.com\Desktop\Test\JSON"

def parse_date(date_str: str) -> Optional[datetime.date]:
    if not date_str:
        return None
    date_str = str(date_str).strip()
    date_str = date_str.replace('/', '-')
    
    formats = [
        "%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ",
        "%d-%m-%y", "%m-%d-%Y"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None

def dates_match(d1: str, d2: str) -> bool:
    pd1 = parse_date(d1)
    pd2 = parse_date(d2)
    if pd1 and pd2:
        return pd1 == pd2
    return False

def exact_float_match(f1, f2) -> bool:
    if f1 is None or f2 is None:
        return False
    try:
        return abs(float(f1) - float(f2)) < 0.001
    except (ValueError, TypeError):
        return False

def check_receipt_cascading(receipts, receipt_num, amount, date, customer_name):
    def matches_rule(row, check_receipt=False, check_amount=False, check_date=False, check_customer=False):
        if check_receipt and row.get('RECEIPT_NUMBER', '').strip() != receipt_num:
            return False
        if check_amount and not exact_float_match(row.get('RECEIPT_AMOUNT'), amount):
            return False
        if check_date and not dates_match(row.get('RECEIPT_DATE'), date):
            return False
        if check_customer:
            cust_names = [row.get('BILL_CUSTOMER_NAME', '').strip().lower(), row.get('P_CUSTOMER_NAME', '').strip().lower()]
            if not any(customer_name.lower() in cn for cn in cust_names if cn):
                return False
        return True

    def find_matches(*args, **kwargs):
        return [r for r in receipts if matches_rule(r, *args, **kwargs)]

    has_amount = amount is not None and str(amount).strip()
    has_date = bool(date)
    has_cust = bool(customer_name)

    rules = []
    
    if receipt_num:
        if has_amount:
            rules.append((lambda: find_matches(check_receipt=True, check_amount=True, check_customer=has_cust), "A1"))
        rules.append((lambda: find_matches(check_receipt=True, check_customer=has_cust), "A2"))
        if has_amount and has_date:
            rules.append((lambda: find_matches(check_receipt=True, check_amount=True, check_date=True, check_customer=has_cust), "A3"))
        if has_cust and has_amount:
            rules.append((lambda: find_matches(check_customer=True, check_amount=True), "A4"))
        if has_cust and has_date:
            rules.append((lambda: find_matches(check_customer=True, check_date=True), "A5"))
    else:
        if has_amount and has_date:
            rules.append((lambda: find_matches(check_amount=True, check_date=True, check_customer=has_cust), "B1"))
        if has_cust and has_amount:
            rules.append((lambda: find_matches(check_customer=True, check_amount=True), "B2"))
        if has_cust and has_date:
            rules.append((lambda: find_matches(check_customer=True, check_date=True), "B3"))
            
    for rule_func, rule_name in rules:
        matches = rule_func()
        if len(matches) == 1:
            return matches[0].get("RECEIPT_NUMBER"), rule_name

    return None, None

def check_invoice_cascading(invoices, inv_num, inv_date, inv_amount, doc_num, customer_name):
    def matches_rule(row, check_inv_num=False, substring_inv_num=False, check_date=False, check_doc=False, check_customer=False, check_amount=False):
        r_num = row.get('TRANSACTION_NUMBER', '').strip()
        if check_inv_num and r_num != inv_num:
            return False
        if substring_inv_num and inv_num not in r_num:
            return False
        if check_date and not dates_match(row.get('TRANSACTION_DATE'), inv_date):
            return False
        if check_doc and row.get('DOCUMENT_NUMBER', '').strip() != doc_num:
            return False
        if check_customer:
            cust_names = [row.get('BILL_CUSTOMER_NAME', '').strip().lower(), row.get('SHIP_CUSTOMER_NAME', '').strip().lower()]
            if not any(customer_name.lower() in cn for cn in cust_names if cn):
                return False
        if check_amount:
            amt = row.get('TOTAL_AMOUNTS') or row.get('TRANSACTION_TOTAL') or row.get('AMOUNT_DUE_ORIGINAL')
            if not exact_float_match(amt, inv_amount):
                return False
        return True

    def find_matches(*args, **kwargs):
        return [r for r in invoices if matches_rule(r, *args, **kwargs)]

    rules = []
    
    if inv_num:
        rules.append((lambda: find_matches(check_inv_num=True), "1a"))
        if inv_date:
            rules.append((lambda: find_matches(check_inv_num=True, check_date=True), "1b"))
    
    if doc_num and inv_date:
        rules.append((lambda: find_matches(check_doc=True, check_date=True), "2"))
        
    if inv_num and inv_date:
        rules.append((lambda: find_matches(substring_inv_num=True, check_date=True), "3"))
        
    if customer_name and inv_date and inv_amount is not None and str(inv_amount).strip():
        rules.append((lambda: find_matches(check_customer=True, check_date=True, check_amount=True), "4"))
        
    for rule_func, rule_name in rules:
        matches = rule_func()
        if len(matches) == 1:
            return matches[0].get("TRANSACTION_NUMBER"), rule_name

    return None, None

print("Loading data from CSVs...")
receipts_db = []
with open(receipt_csv, 'r', encoding='utf-8-sig', errors='ignore') as f:
    receipts_db = list(csv.DictReader(f))

invoices_db = []
with open(invoice_csv, 'r', encoding='utf-8-sig', errors='ignore') as f:
    invoices_db = list(csv.DictReader(f))

print(f"Loaded {len(receipts_db)} receipts and {len(invoices_db)} invoices.")

results = []
for json_file in glob.glob(os.path.join(json_dir, "*.json")):
    with open(json_file, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            continue
    
    filename = os.path.basename(json_file)
    
    receipt_num = str(data.get("payment_reference", "")).strip()
    if receipt_num == "None": receipt_num = ""
    receipt_amount = data.get("total_amount")
    receipt_date = str(data.get("payment_date", "")).strip()
    if receipt_date == "None": receipt_date = ""
    customer_name = str(data.get("customer_name", "")).strip()
    if customer_name == "None": customer_name = ""
    
    matched_receipt, receipt_rule = check_receipt_cascading(receipts_db, receipt_num, receipt_amount, receipt_date, customer_name)
        
    invoice_matches = []
    for inv in data.get("invoices", []):
        inv_num = str(inv.get("invoice_number", "")).strip()
        if inv_num == "None": inv_num = ""
        inv_date = str(inv.get("invoice_date", "")).strip()
        if inv_date == "None": inv_date = ""
        inv_amount = inv.get("invoice_amount")
        doc_num = str(inv.get("customer_invoice_number", "")).strip()
        if doc_num == "None": doc_num = ""
        
        matched_inv, inv_rule = check_invoice_cascading(invoices_db, inv_num, inv_date, inv_amount, doc_num, customer_name)
        invoice_matches.append({
            "original_num": inv_num,
            "matched_num": matched_inv,
            "rule": inv_rule
        })
        
    results.append({
        "file": filename,
        "receipt_number": receipt_num,
        "matched_receipt": matched_receipt,
        "receipt_rule": receipt_rule,
        "invoices": invoice_matches
    })

print("\n--- CASCADING RECONCILIATION RESULTS ---")
for res in results:
    print(f"File: {res['file']}")
    if res['matched_receipt']:
        print(f"  Receipt ({res['receipt_number']}): FOUND as {res['matched_receipt']} (Rule {res['receipt_rule']})")
    else:
        print(f"  Receipt ({res['receipt_number']}): NOT FOUND")
        
    for inv in res['invoices']:
        if inv['matched_num']:
            print(f"    Invoice ({inv['original_num']}): FOUND as {inv['matched_num']} (Rule {inv['rule']})")
        else:
            print(f"    Invoice ({inv['original_num']}): NOT FOUND")
    print("-" * 30)
