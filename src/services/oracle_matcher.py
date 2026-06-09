import asyncio
import urllib.parse
import os
from typing import Dict, Any, Optional

from src.utils.date_formatter import format_oracle_date

ORACLE_URL = "https://fa-epxp-test-saasfaprod1.fa.ocs.oraclecloud.com"

async def fetch_oracle_query(client, username, password, endpoint, idx, identifier):
    """
    Executes a standard REST GET to Oracle ERP.
    Returns (idx, item_dict, is_match)
    """
    try:
        res = await client.get(endpoint, auth=(username, password))
        if res.status_code == 200:
            data = res.json()
            items = data.get("items", [])
            if len(items) == 1:
                item = items[0]
                return idx, {
                    "matched_in_oracle": True,
                    "item": item,
                    "error": None
                }, True
        elif res.status_code in (401, 403):
            return idx, {"identifier": identifier, "matched_in_oracle": False, "error": f"HTTP {res.status_code}"}, False
        return idx, None, False
    except Exception as e:
        return idx, {"identifier": identifier, "matched_in_oracle": False, "error": str(e)}, False

async def check_receipt_cascading(client, username, password, receipt_num, amount, date, customer_name) -> Dict[str, Any]:
    """
    Executes cascading rules A1-A5 and B1-B3 to find the exact Oracle standardReceipt record.
    """
    queries = []
    
    date = format_oracle_date(date)
    cust_filter = f" and CustomerName='{customer_name}'" if customer_name else ""
    
    # Scenario A
    if receipt_num:
        if amount is not None and str(amount).strip():
            queries.append(f"ReceiptNumber='{receipt_num}' and Amount={amount}{cust_filter}")
        queries.append(f"ReceiptNumber='{receipt_num}'{cust_filter}")
        if amount is not None and str(amount).strip() and date:
            queries.append(f"ReceiptNumber='{receipt_num}' and Amount={amount} and ReceiptDate='{date}'{cust_filter}")
        if customer_name and amount is not None and str(amount).strip():
            queries.append(f"CustomerName='{customer_name}' and Amount={amount}")
        if customer_name and date:
            queries.append(f"CustomerName='{customer_name}' and ReceiptDate='{date}'")
    # Scenario B
    else:
        if amount is not None and str(amount).strip() and date:
            queries.append(f"Amount={amount} and ReceiptDate='{date}'{cust_filter}")
        if customer_name and amount is not None and str(amount).strip():
            queries.append(f"CustomerName='{customer_name}' and Amount={amount}")
        if customer_name and date:
            queries.append(f"CustomerName='{customer_name}' and ReceiptDate='{date}'")
            
    if not queries:
        return {"receipt_reference": receipt_num, "matched_in_oracle": False, "error": "No receipt query rules could be formed"}

    tasks = []
    for idx, q in enumerate(queries):
        encoded_q = urllib.parse.quote(q)
        endpoint = f"{ORACLE_URL}/fscmRestApi/resources/11.13.18.05/standardReceipts?q={encoded_q}"
        tasks.append(fetch_oracle_query(client, username, password, endpoint, idx, receipt_num))
        
    results = await asyncio.gather(*tasks)
    results.sort(key=lambda x: x[0]) # Enforce cascading priority
    
    for idx, result_dict, is_match in results:
        if is_match:
            item = result_dict["item"]
            return {
                "matched_in_oracle": True,
                "fusion_receipt_number": item.get("ReceiptNumber"),
                "fusion_receipt_date": item.get("ReceiptDate"),
                "fusion_customer_name": item.get("CustomerName"),
                "error": None
            }
        if result_dict and result_dict.get("error"):
            return result_dict

    return {"receipt_reference": receipt_num, "matched_in_oracle": False, "error": "No single match found after cascading rules"}

async def check_invoice_cascading(client, username, password, inv_num, inv_date, inv_amount, doc_num, customer_name) -> Dict[str, Any]:
    """
    Executes cascading rules 1a, 1b, 2, 3, 4 to find the exact Oracle receivablesInvoice record.
    """
    queries = []
    
    inv_date = format_oracle_date(inv_date)
    
    if inv_num:
        queries.append(f"TrxNumber='{inv_num}'")
        if inv_date:
            queries.append(f"TrxNumber='{inv_num}' and TrxDate='{inv_date}'")
    
    if doc_num and inv_date:
        queries.append(f"CustomerReference='{doc_num}' and TrxDate='{inv_date}'")
        
    if inv_num and inv_date:
        queries.append(f"TrxNumber LIKE '%{inv_num}%' and TrxDate='{inv_date}'")
        
    if customer_name and inv_date and inv_amount is not None and str(inv_amount).strip():
        queries.append(f"BillToCustomerName='{customer_name}' and TrxDate='{inv_date}' and InvoiceAmount={inv_amount}")
        
    if not queries:
        return {"invoice_number": inv_num, "matched_in_oracle": False, "error": "No invoice query rules could be formed"}

    tasks = []
    for idx, q in enumerate(queries):
        encoded_q = urllib.parse.quote(q)
        endpoint = f"{ORACLE_URL}/fscmRestApi/resources/11.13.18.05/receivablesInvoices?q={encoded_q}"
        tasks.append(fetch_oracle_query(client, username, password, endpoint, idx, inv_num))
        
    results = await asyncio.gather(*tasks)
    results.sort(key=lambda x: x[0])
    
    for idx, result_dict, is_match in results:
        if is_match:
            item = result_dict["item"]
            return {
                "matched_in_oracle": True,
                "fusion_invoice_number": item.get("TrxNumber"),
                "fusion_invoice_date": item.get("TrxDate"),
                "fusion_invoice_amount": item.get("InvoiceAmount"),
                "error": None
            }
        if result_dict and result_dict.get("error"):
            return result_dict

    return {"invoice_number": inv_num, "matched_in_oracle": False, "error": "No single match found after cascading rules"}
