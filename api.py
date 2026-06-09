from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Any
import httpx
import asyncio
import time
import os
import urllib.parse
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("reconciliation_api")

# Global HTTP client
http_client: Optional[httpx.AsyncClient] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    # Timeout set to 15s (below typical 60s Vercel limit) to fail gracefully
    http_client = httpx.AsyncClient(timeout=15.0)
    logger.info("Starting up global HTTP client")
    yield
    logger.info("Shutting down global HTTP client")
    await http_client.aclose()

app = FastAPI(title="Oracle Reconciliation Live API", version="3.0.0", lifespan=lifespan)

# Setup CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ORACLE_URL = "https://fa-epxp-test-saasfaprod1.fa.ocs.oraclecloud.com"

class InvoiceItem(BaseModel):
    Line_ID: Optional[str] = None
    invoice_number: Optional[str] = None
    fusion_invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    fusion_invoice_date: Optional[str] = None
    invoice_amount: Optional[float] = None
    fusion_invoice_amount: Optional[float] = None
    description: Optional[str] = None
    customer_invoice_number: Optional[str] = None
    storeNo: Optional[str] = None

class MetaInfo(BaseModel):
    file_kind: Optional[str] = None
    filename: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    api_calls: Optional[int] = None
    response_time_ms: Optional[float] = None
    num_pages: Optional[int] = None
    warnings: List[str] = []

class ReconciliationRequest(BaseModel):
    customer_name: Optional[str] = None
    fusion_customer_name: Optional[str] = None
    payment_reference: Optional[str] = None
    fusion_receipt_number: Optional[str] = None
    payment_date: Optional[str] = None
    fusion_receipt_date: Optional[str] = None
    header_id: Optional[str] = None
    invoices: List[InvoiceItem] = []
    total_amount: Optional[float] = None
    confidence_score: Optional[float] = None
    confidence_label: Optional[str] = None
    invoice_count: Optional[int] = None
    _meta: Optional[dict] = None

async def check_receipt_cascading(client, username, password, receipt_num, amount, date, customer_name):
    queries = []
    
    # Scenario A
    if receipt_num:
        if amount is not None and str(amount).strip():
            queries.append(f"ReceiptNumber='{receipt_num}' and Amount={amount}")
        queries.append(f"ReceiptNumber='{receipt_num}'")
        if amount is not None and str(amount).strip() and date:
            queries.append(f"ReceiptNumber='{receipt_num}' and Amount={amount} and ReceiptDate='{date}'")
        if customer_name and amount is not None and str(amount).strip():
            queries.append(f"CustomerName='{customer_name}' and Amount={amount}")
        if customer_name and date:
            queries.append(f"CustomerName='{customer_name}' and ReceiptDate='{date}'")
    # Scenario B
    else:
        if amount is not None and str(amount).strip() and date:
            queries.append(f"Amount={amount} and ReceiptDate='{date}'")
        if customer_name and amount is not None and str(amount).strip():
            queries.append(f"CustomerName='{customer_name}' and Amount={amount}")
        if customer_name and date:
            queries.append(f"CustomerName='{customer_name}' and ReceiptDate='{date}'")
            
    if not queries:
        return {"receipt_reference": receipt_num, "matched_in_oracle": False, "error": "No receipt query rules could be formed"}

    async def fetch_query(idx, q):
        encoded_q = urllib.parse.quote(q)
        endpoint = f"{ORACLE_URL}/fscmRestApi/resources/11.13.18.05/standardReceipts?q={encoded_q}"
        try:
            res = await client.get(endpoint, auth=(username, password))
            if res.status_code == 200:
                data = res.json()
                items = data.get("items", [])
                if len(items) == 1:
                    return idx, {"receipt_reference": receipt_num, "matched_in_oracle": True, "method": f"Rule A/B {idx+1}", "query": q, "error": None}, True
            elif res.status_code in (401, 403):
                return idx, {"receipt_reference": receipt_num, "matched_in_oracle": False, "error": f"HTTP {res.status_code}"}, False
            return idx, None, False
        except Exception as e:
            return idx, {"receipt_reference": receipt_num, "matched_in_oracle": False, "error": str(e)}, False

    tasks = [fetch_query(idx, q) for idx, q in enumerate(queries)]
    results = await asyncio.gather(*tasks)
    results.sort(key=lambda x: x[0])
    
    for idx, result_dict, is_match in results:
        if is_match:
            return result_dict
        if result_dict and result_dict.get("error"):
            return result_dict

    return {"receipt_reference": receipt_num, "matched_in_oracle": False, "error": "No single match found after cascading rules"}

async def check_invoice_cascading(client, username, password, inv_num, inv_date, inv_amount, doc_num, customer_name):
    queries = []
    
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

    async def fetch_invoice_query(idx, q):
        encoded_q = urllib.parse.quote(q)
        endpoint = f"{ORACLE_URL}/fscmRestApi/resources/11.13.18.05/receivablesInvoices?q={encoded_q}"
        try:
            res = await client.get(endpoint, auth=(username, password))
            if res.status_code == 200:
                data = res.json()
                items = data.get("items", [])
                if len(items) == 1:
                    return idx, {"invoice_number": inv_num, "matched_in_oracle": True, "method": f"Rule {idx+1}", "query": q, "error": None}, True
            elif res.status_code in (401, 403):
                return idx, {"invoice_number": inv_num, "matched_in_oracle": False, "error": f"HTTP {res.status_code}"}, False
            return idx, None, False
        except Exception as e:
            return idx, {"invoice_number": inv_num, "matched_in_oracle": False, "error": str(e)}, False

    tasks = [fetch_invoice_query(idx, q) for idx, q in enumerate(queries)]
    results = await asyncio.gather(*tasks)
    results.sort(key=lambda x: x[0])
    
    for idx, result_dict, is_match in results:
        if is_match:
            return result_dict
        if result_dict and result_dict.get("error"):
            return result_dict

    return {"invoice_number": inv_num, "matched_in_oracle": False, "error": "No single match found after cascading rules"}

@app.get("/")
async def root():
    return {"status": "online", "message": "Oracle Reconciliation API is running"}

@app.post("/reconcile")
async def reconcile_data(payload: ReconciliationRequest):
    """
    Endpoint for real-time Oracle matching using Cascading Rules.
    """
    logger.info(f"Received reconcile request for payment_reference: {payload.payment_reference}")
    
    x_oracle_user = os.getenv("ORACLE_USER")
    x_oracle_pass = os.getenv("ORACLE_PASS")
    
    if not x_oracle_user or not x_oracle_pass or x_oracle_user == "YOUR_USERNAME_HERE":
        logger.error("Oracle credentials are not configured in the .env file.")
        raise HTTPException(status_code=500, detail="Oracle credentials are not configured in the .env file.")
        
    if not http_client:
        logger.error("Global HTTP client is not initialized")
        raise HTTPException(status_code=500, detail="Internal server error: HTTP client not initialized")
        
    start_time = time.time()
    
    receipt_num = str(payload.payment_reference).strip() if payload.payment_reference is not None else ""
    if receipt_num == "None":
        receipt_num = ""
    receipt_amount = payload.total_amount
    receipt_date = str(payload.payment_date).strip() if payload.payment_date is not None else ""
    if receipt_date == "None":
        receipt_date = ""
    customer_name = str(payload.customer_name).strip() if payload.customer_name is not None else ""
    if customer_name == "None":
        customer_name = ""

    # 1. Check Receipt (Cascading)
    receipt_result = await check_receipt_cascading(
        http_client, x_oracle_user, x_oracle_pass, receipt_num, receipt_amount, receipt_date, customer_name
    )
    
    if receipt_result.get("error"):
        logger.warning(f"Receipt match error or not found: {receipt_result.get('error')}")
            
    # 2. Check Invoices concurrently (Each invoice has cascading rules)
    tasks = []
    for inv in payload.invoices:
        inv_num = str(inv.invoice_number).strip() if inv.invoice_number is not None else ""
        if inv_num == "None":
            inv_num = ""
        inv_date = str(inv.invoice_date).strip() if inv.invoice_date is not None else ""
        if inv_date == "None":
            inv_date = ""
        inv_amount = inv.invoice_amount
        doc_num = str(inv.customer_invoice_number).strip() if inv.customer_invoice_number is not None else ""
        if doc_num == "None":
            doc_num = ""
            
        tasks.append(check_invoice_cascading(
            http_client, x_oracle_user, x_oracle_pass, inv_num, inv_date, inv_amount, doc_num, customer_name
        ))
        
    invoice_results = await asyncio.gather(*tasks)
    
    execution_time = round(time.time() - start_time, 2)
    logger.info(f"Reconciliation completed in {execution_time}s. Invoices checked: {len(invoice_results)}")
        
    # 3. Return the results
    return {
        "status": "success",
        "message": "Live Oracle REST API check complete using cascading rules",
        "execution_time_seconds": execution_time,
        "receipt_reference": receipt_num,
        "receipt_matched_in_oracle": receipt_result.get("matched_in_oracle"),
        "receipt_method_used": receipt_result.get("method", ""),
        "receipt_error": receipt_result.get("error"),
        "invoices_checked": len(invoice_results),
        "invoice_details": invoice_results
    }
