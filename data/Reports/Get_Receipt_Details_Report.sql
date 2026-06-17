SELECT
    hp.party_name                           AS bill_customer_name,
    hca.account_number                      AS bill_customer_number,
    hou.name                                AS business_unit,
    arm.name                                AS receipt_method,
    acr.receipt_number                      AS receipt_number,
    TO_CHAR(acr.receipt_date,'DD-MM-YYYY')  AS receipt_date,
    ''                                      AS application_type,
    acr.amount                              AS receipt_amount,
    nvl((select sum(AMOUNT_APPLIED)
     from ar_receivable_applications_all araa
     where 1=1
     AND UPPER(NVL(araa.display,'Y')) = 'Y'
     and araa.CASH_RECEIPT_ID = acr.CASH_RECEIPT_ID),0)  AS applied_amount,
    (select acr.amount - sum(AMOUNT_APPLIED)
     from ar_receivable_applications_all araa
     where 1=1
     AND UPPER(NVL(araa.display,'Y')) = 'Y'
     and araa.CASH_RECEIPT_ID = acr.CASH_RECEIPT_ID)  AS unapplied_amount,
    acr.currency_code                       AS currency,
    acr.status                              AS receipt_status_code
FROM
    ar_cash_receipts_all             acr,
    ar_receipt_methods               arm,
    hz_cust_accounts                 hca,
    hz_parties                       hp,
    hr_organization_units            hou
WHERE 1 = 1
    -- Customer
    AND acr.pay_from_customer       = hca.cust_account_id(+)
    AND hca.party_id                = hp.party_id(+)
    -- Receipt Method
    AND acr.receipt_method_id       = arm.receipt_method_id(+)
    -- Business Unit
    AND acr.org_id                  = hou.organization_id(+)
    
    -- DYNAMIC FILTERING LOGIC
    AND (
        -- Base case: If no parameters are passed, return ALL UNAPP / UNID receipts
        ( acr.status IN ('UNID','UNAPP')
          AND :P_RECEIPT_NUMBER IS NULL
          AND :P_CUSTOMER_NAME IS NULL
          AND :P_RECEIPT_DATE IS NULL
          AND :P_RECEIPT_AMOUNT IS NULL
        )
        OR
        -- Parameterized case: If ANY parameter is passed, search ALL receipts regardless of status
        (
            ( :P_RECEIPT_NUMBER IS NOT NULL OR :P_CUSTOMER_NAME IS NOT NULL OR :P_RECEIPT_DATE IS NOT NULL OR :P_RECEIPT_AMOUNT IS NOT NULL )
            AND (
                (hp.party_name = :P_CUSTOMER_NAME AND :P_CUSTOMER_NAME IS NOT NULL)
                OR (acr.receipt_number LIKE '%'||:P_RECEIPT_NUMBER||'%' AND :P_RECEIPT_NUMBER IS NOT NULL)
                OR (TO_DATE(TO_CHAR(:P_RECEIPT_DATE,'DD-MM-YYYY'),'DD-MM-YYYY') BETWEEN trunc(acr.receipt_date-3) AND trunc(acr.receipt_date+3) AND :P_RECEIPT_DATE IS NOT NULL)
                OR (acr.amount = :P_RECEIPT_AMOUNT AND :P_RECEIPT_AMOUNT IS NOT NULL)
            )
        )
    )
