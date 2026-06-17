import json
with open('data/JSON/Large-4.json', 'r') as fp:
    data = json.load(fp)
    print(f"Customer Name: {data.get('customer_name')}")
    print(f"Payment Date: {data.get('payment_date')}")
    print(f"Total Amount: {data.get('fusion_applied_amount')}")
