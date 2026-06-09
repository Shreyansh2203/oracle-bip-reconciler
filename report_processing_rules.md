

### Amount match should always be exact 

Exact float equality everywhere — no tolerance, no rounding:

## RULE 2 — Find the Receipt

### SCENARIO A (`payment_reference` is present)
Stop the moment any step yields exactly 1 match.

#### A1 — Receipt number + amount (+ optional customer name)


#### A2 — Receipt number only (+ optional customer name)

#### A3 — Receipt number + amount + date (+ optional customer name)


#### A4 — Customer name + amount (payment_reference abandoned)


#### A5 — Customer name + date (last resort)


### SCENARIO B (`payment_reference` is null or empty)
Stop the moment any step yields exactly 1 match.

#### B1 — Amount + date (+ optional customer name)


#### B2 — Customer name + amount


#### B3 — Customer name + date (last resort)












## RULE 3 — Find the Invoice



### Step 1a — Exact invoice number only


### Step 1b — Exact invoice number + date


### Step 2 — Customer document number + date


### Step 3 — Substring invoice number + date


### Step 4 — Customer name + date + amount (last resort)



