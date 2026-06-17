import json
with open('data/JSON/new 2.json', 'r') as fp:
    data = json.load(fp)
    print(f"Customer Name: {data.get('customer_name')}")
    print(f"Payment Date: {data.get('payment_date')}")
    print(f"Total Amount: {data.get('total_amount')}")
    print(f"Payment Reference: {data.get('payment_reference')}")
