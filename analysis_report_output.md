# JSON Payload Analysis Report

## 1.json
**Result:** 🟢 SUCCESS (7/7)
**Reason:** All invoices perfectly matched Oracle ledger for Galati Fresh Market.

## 2.json
**Result:** 🟢 SUCCESS (7/7)
**Reason:** All invoices perfectly matched Oracle ledger for Galati Fresh Market.

## 3.json
**Result:** 🟢 SUCCESS (516/516)
**Reason:** All invoices perfectly matched Oracle ledger for Macs Convenience Stores.

## 4.json
**Result:** 🟡 PARTIAL (0/3)
**Customer Identified:** New Horizon Foods
**Reason for Failure:** Some invoices failed the 3-way, 2-way, and 1-way safety fallback checks. Below is an analysis of why:

- **Payload Invoice:** Num: `225812831804`, Date: `2025/11/14`, Amt: `51.2`
  - **Why it failed:** This invoice does not exist in the Oracle ledger for New Horizon Foods. The OCR completely hallucinated this invoice or assigned it to the wrong customer.

- **Payload Invoice:** Num: `2258128318041`, Date: `2026/05/02`, Amt: `100.0`
  - **Why it failed:** This invoice does not exist in the Oracle ledger for New Horizon Foods. The OCR completely hallucinated this invoice or assigned it to the wrong customer.

- **Payload Invoice:** Num: `2258128318042`, Date: `2026/05/01`, Amt: `200.0`
  - **Why it failed:** This invoice does not exist in the Oracle ledger for New Horizon Foods. The OCR completely hallucinated this invoice or assigned it to the wrong customer.


## 5.json
**Result:** 🟡 PARTIAL (0/3)
**Customer Identified:** New Horizon Foods
**Reason for Failure:** Some invoices failed the 3-way, 2-way, and 1-way safety fallback checks. Below is an analysis of why:

- **Payload Invoice:** Num: `225812831804`, Date: `2025/11/14`, Amt: `51.2`
  - **Why it failed:** This invoice does not exist in the Oracle ledger for New Horizon Foods. The OCR completely hallucinated this invoice or assigned it to the wrong customer.

- **Payload Invoice:** Num: `2258128318041`, Date: `2026/05/02`, Amt: `100.0`
  - **Why it failed:** This invoice does not exist in the Oracle ledger for New Horizon Foods. The OCR completely hallucinated this invoice or assigned it to the wrong customer.

- **Payload Invoice:** Num: `2258128318042`, Date: `2026/05/01`, Amt: `200.0`
  - **Why it failed:** This invoice does not exist in the Oracle ledger for New Horizon Foods. The OCR completely hallucinated this invoice or assigned it to the wrong customer.


## 6.json
**Result:** 🔴 FAILED (Customer Not Found)
**Reason:** The payment reference and all invoice numbers provided in the payload do not exist anywhere in the Oracle BI Publisher reports. This usually means the JSON payload contains hallucinated OCR data or refers to an entirely different customer/ledger that is not in the system.

## 7.json
**Result:** 🔴 FAILED (Customer Not Found)
**Reason:** The payment reference and all invoice numbers provided in the payload do not exist anywhere in the Oracle BI Publisher reports. This usually means the JSON payload contains hallucinated OCR data or refers to an entirely different customer/ledger that is not in the system.

## 9.json
**Result:** 🟡 PARTIAL (0/3)
**Customer Identified:** New Horizon Foods
**Reason for Failure:** Some invoices failed the 3-way, 2-way, and 1-way safety fallback checks. Below is an analysis of why:

- **Payload Invoice:** Num: `225812831804`, Date: `2025/11/14`, Amt: `51.2`
  - **Why it failed:** This invoice does not exist in the Oracle ledger for New Horizon Foods. The OCR completely hallucinated this invoice or assigned it to the wrong customer.

- **Payload Invoice:** Num: `2258128318041`, Date: `2026/05/02`, Amt: `100.0`
  - **Why it failed:** This invoice does not exist in the Oracle ledger for New Horizon Foods. The OCR completely hallucinated this invoice or assigned it to the wrong customer.

- **Payload Invoice:** Num: `2258128318042`, Date: `2026/05/01`, Amt: `200.0`
  - **Why it failed:** This invoice does not exist in the Oracle ledger for New Horizon Foods. The OCR completely hallucinated this invoice or assigned it to the wrong customer.


## 10.json
**Result:** 🔴 FAILED (Customer Not Found)
**Reason:** The payment reference and all invoice numbers provided in the payload do not exist anywhere in the Oracle BI Publisher reports. This usually means the JSON payload contains hallucinated OCR data or refers to an entirely different customer/ledger that is not in the system.

## 11.json
**Result:** 🟡 PARTIAL (0/3)
**Customer Identified:** New Horizon Foods
**Reason for Failure:** Some invoices failed the 3-way, 2-way, and 1-way safety fallback checks. Below is an analysis of why:

- **Payload Invoice:** Num: `225812831804`, Date: `2025/11/14`, Amt: `51.2`
  - **Why it failed:** This invoice does not exist in the Oracle ledger for New Horizon Foods. The OCR completely hallucinated this invoice or assigned it to the wrong customer.

- **Payload Invoice:** Num: `2258128318041`, Date: `2026/05/02`, Amt: `100.0`
  - **Why it failed:** This invoice does not exist in the Oracle ledger for New Horizon Foods. The OCR completely hallucinated this invoice or assigned it to the wrong customer.

- **Payload Invoice:** Num: `2258128318042`, Date: `2026/05/01`, Amt: `200.0`
  - **Why it failed:** This invoice does not exist in the Oracle ledger for New Horizon Foods. The OCR completely hallucinated this invoice or assigned it to the wrong customer.


## 12.json
**Result:** 🟡 PARTIAL (1/2)
**Customer Identified:** 7-Eleven Distribution Canada Corp
**Reason for Failure:** Some invoices failed the 3-way, 2-way, and 1-way safety fallback checks. Below is an analysis of why:

- **Payload Invoice:** Num: `None`, Date: `2026-02-09`, Amt: `1000.0`
  - **Why it failed:** The closest match in Oracle was `Num: 4130, Amt: 1000, Date: 2026-02-09`. The OCR data was too corrupted (failed 2-way and exact 1-way match), so the engine safely refused to map it.


## 13.json
**Result:** 🔴 FAILED (Customer Not Found)
**Reason:** The payment reference and all invoice numbers provided in the payload do not exist anywhere in the Oracle BI Publisher reports. This usually means the JSON payload contains hallucinated OCR data or refers to an entirely different customer/ledger that is not in the system.

## 14.json
**Result:** 🟢 SUCCESS (3/3)
**Reason:** All invoices perfectly matched Oracle ledger for New Horizon Foods.

## 15.json
**Result:** 🟢 SUCCESS (493/493)
**Reason:** All invoices perfectly matched Oracle ledger for Macs Convenience Stores.

## 16.json
**Result:** 🟢 SUCCESS (493/493)
**Reason:** All invoices perfectly matched Oracle ledger for Macs Convenience Stores.

## 17.json
**Result:** 🟡 PARTIAL (493/498)
**Customer Identified:** Macs Convenience Stores
**Reason for Failure:** Some invoices failed the 3-way, 2-way, and 1-way safety fallback checks. Below is an analysis of why:

- **Payload Invoice:** Num: `5884219948A2507`, Date: `2025-07-21`, Amt: `-4.78`
  - **Why it failed:** This invoice does not exist in the Oracle ledger for Macs Convenience Stores. The OCR completely hallucinated this invoice or assigned it to the wrong customer.

- **Payload Invoice:** Num: `6650306773378`, Date: `2026-02-02`, Amt: `209.15`
  - **Why it failed:** The closest match in Oracle was `Num: 6650303378, Amt: 209.15, Date: 2026-02-02`. The OCR data was too corrupted (failed 2-way and exact 1-way match), so the engine safely refused to map it.

- **Payload Invoice:** Num: `6650303378`, Date: `2026-02-03`, Amt: `209.15`
  - **Why it failed:** The closest match in Oracle was `Num: 6650303378, Amt: 209.15, Date: 2026-02-02`. The OCR data was too corrupted (failed 2-way and exact 1-way match), so the engine safely refused to map it.

- **Payload Invoice:** Num: `665039803378`, Date: `2026-02-02`, Amt: `220.15`
  - **Why it failed:** The closest match in Oracle was `Num: 6650503308, Amt: 36.75, Date: 2026-02-02`. The OCR data was too corrupted (failed 2-way and exact 1-way match), so the engine safely refused to map it.

- **Payload Invoice:** Num: `None`, Date: `2026-02-02`, Amt: `209.15`
  - **Why it failed:** The closest match in Oracle was `Num: 6650303378, Amt: 209.15, Date: 2026-02-02`. The OCR data was too corrupted (failed 2-way and exact 1-way match), so the engine safely refused to map it.


## 18.json
**Result:** 🟡 PARTIAL (494/498)
**Customer Identified:** Macs Convenience Stores
**Reason for Failure:** Some invoices failed the 3-way, 2-way, and 1-way safety fallback checks. Below is an analysis of why:

- **Payload Invoice:** Num: `5884219948A2507`, Date: `2025-07-21`, Amt: `-4.78`
  - **Why it failed:** This invoice does not exist in the Oracle ledger for Macs Convenience Stores. The OCR completely hallucinated this invoice or assigned it to the wrong customer.

- **Payload Invoice:** Num: `6650303378`, Date: `2026-02-03`, Amt: `209.15`
  - **Why it failed:** The closest match in Oracle was `Num: 6650303378, Amt: 209.15, Date: 2026-02-02`. The OCR data was too corrupted (failed 2-way and exact 1-way match), so the engine safely refused to map it.

- **Payload Invoice:** Num: `665039803378`, Date: `2026-02-02`, Amt: `220.15`
  - **Why it failed:** The closest match in Oracle was `Num: 6650503308, Amt: 36.75, Date: 2026-02-02`. The OCR data was too corrupted (failed 2-way and exact 1-way match), so the engine safely refused to map it.

- **Payload Invoice:** Num: `None`, Date: `2026-02-02`, Amt: `209.15`
  - **Why it failed:** The closest match in Oracle was `Num: 6650303378, Amt: 209.15, Date: 2026-02-02`. The OCR data was too corrupted (failed 2-way and exact 1-way match), so the engine safely refused to map it.


## 20.json
**Result:** 🔴 FAILED (Customer Not Found)
**Reason:** The payment reference and all invoice numbers provided in the payload do not exist anywhere in the Oracle BI Publisher reports. This usually means the JSON payload contains hallucinated OCR data or refers to an entirely different customer/ledger that is not in the system.

## 21.json
**Result:** 🟢 SUCCESS (516/516)
**Reason:** All invoices perfectly matched Oracle ledger for Macs Convenience Stores.

## 22.json
**Result:** 🟢 SUCCESS (3/3)
**Reason:** All invoices perfectly matched Oracle ledger for New Horizon Foods.
