import os, json
for f in os.listdir('data/JSON'):
    with open(os.path.join('data/JSON', f), 'r') as fp:
        data = json.load(fp)
        invoices = [i['invoice_number'] for i in data.get('invoices', [])]
        print(f"{f}: REC={data.get('payment_reference')} | INV={invoices}")
