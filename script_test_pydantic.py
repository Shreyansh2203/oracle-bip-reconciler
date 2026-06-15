from src.models import ReconciliationRequest

# Read the JSON from the user's previous message (I'll just paste a subset here)
data = {
  "customer_name": "Macs Convenience Stores",
  "payment_reference": "E00000000411615",
  "payment_date": "2026-02-25",
  "header_id": 300000053408953,
  "invoices": [
    {
      "Line_ID": 300000053406231,
      "invoice_number": "6542203601",
      "invoice_date": "2026-02-05",
      "invoice_amount": 265.48,
      "customer_invoice_number": "",
      "storeNo": "4"
    },
    {
      "Line_ID": 300000053406232,
      "invoice_number": "6542203602",
      "invoice_date": "2026-02-05",
      "invoice_amount": None,
      "customer_invoice_number": "",
      "storeNo": "4"
    }
  ]
}

try:
    req = ReconciliationRequest(**data)
    print("Success. Invoice count:", len(req.invoices))
except Exception as e:
    print("Failed:")
    print(e)
