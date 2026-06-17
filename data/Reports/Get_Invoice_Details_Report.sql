SELECT
    hp_bill.party_name               AS bill_customer_name,      
    hca_bill.account_number          AS bill_account_number,
    hcsu_bill_to.location            AS bill_site,
    hps_bill.party_site_number       AS bill_party_site_number,
    hp_ship.party_name               AS ship_customer_name,  
    hps_ship.party_site_number       AS ship_site_number,
    hou.name                         AS business_unit,
    types.type                       AS Invoice_type,														
    types.name                       AS transaction_type_name,
    trx.trx_number                   AS transaction_number,		
    trx.doc_sequence_value           AS document_number,
    trx.invoice_currency_code        AS currency,
    TO_DATE(TO_CHAR(trx.trx_date,'DD-MM-YYYY'),'DD-MM-YYYY') AS transaction_date,
    NVL(
        (SELECT SUM(lines.extended_amount)
         FROM ra_customer_trx_lines_all lines
         WHERE lines.customer_trx_id = trx.customer_trx_id),
        0
    )                                AS transaction_total,
    ps.amount_due_original           AS total_amounts,
    batch.name                       AS batch_source_name,   
    ps.amount_due_original,
    ps.amount_due_remaining,
    CASE 
        WHEN abs(ps.amount_due_remaining) > 0 THEN 'OPEN'
        WHEN ps.amount_due_remaining = 0 THEN 'CLOSED'
        ELSE 'OTHER'
    END AS INVOICE_STATUS
FROM
    ra_customer_trx_all         trx,
    ra_cust_trx_types_all       types,
    ra_batch_sources_all        batch,
    ar_payment_schedules_all    ps,
    ra_terms_lines              tl,
    ra_terms                    t,
    hz_cust_accounts            hca_bill,
    hz_parties                  hp_bill,
    hz_party_sites              hps_bill,
    hz_cust_acct_sites_all      hcasa_bill,
    hz_cust_site_uses_all       hcsu_bill_to,
    hz_parties                  hp_ship,          
    hz_party_site_uses          hps_ship_to,
    hz_party_sites              hps_ship,
    hr_organization_units       hou
WHERE 1=1
    AND trx.bill_to_customer_id         = hca_bill.cust_account_id
    AND hca_bill.party_id               = hp_bill.party_id
    AND trx.cust_trx_type_seq_id        = types.cust_trx_type_seq_id
    AND trx.batch_source_seq_id         = batch.batch_source_seq_id
    -- Payment Schedule
    AND trx.customer_trx_id             = ps.customer_trx_id
    AND trx.term_id                     = tl.term_id(+)
    AND trx.term_id                     = t.term_id(+)
    -- BILL TO
    AND trx.bill_to_site_use_id         = hcsu_bill_to.site_use_id
    AND hcsu_bill_to.cust_acct_site_id  = hcasa_bill.cust_acct_site_id
    AND hcasa_bill.party_site_id        = hps_bill.party_site_id
    -- SHIP TO
    AND trx.ship_to_party_id            = hp_ship.party_id(+)   
    AND trx.ship_to_party_site_use_id   = hps_ship_to.party_site_use_id(+)
    AND hps_ship_to.party_site_id       = hps_ship.party_site_id(+)
    AND trx.org_id                      = hou.organization_id
    
    -- DYNAMIC FILTERING LOGIC
    AND (
        -- Base case: If no parameters are passed, return ALL OPEN Invoices
        ( abs(ps.amount_due_remaining) > 0
          AND TRIM(:P_CUSTOMER_NAME) IS NULL
          AND TRIM(:P_INVOICE_NUM) IS NULL
          AND TRIM(:P_INVOICE_DATE) IS NULL
          AND TRIM(:P_INVOICE_AMOUNT) IS NULL
        )
        OR
        -- Parameterized case: If ANY parameter is passed, search BOTH Open and Closed Invoices
        (
            ( TRIM(:P_CUSTOMER_NAME) IS NOT NULL OR TRIM(:P_INVOICE_NUM) IS NOT NULL OR TRIM(:P_INVOICE_DATE) IS NOT NULL OR TRIM(:P_INVOICE_AMOUNT) IS NOT NULL )
            AND (
                (hp_bill.party_name = :P_CUSTOMER_NAME AND TRIM(:P_CUSTOMER_NAME) IS NOT NULL)
                OR (trx.trx_number LIKE '%'||TRIM(:P_INVOICE_NUM)||'%' AND TRIM(:P_INVOICE_NUM) IS NOT NULL)
                OR (TRUNC(trx.trx_date) BETWEEN (TO_DATE(NVL(TRIM(:P_INVOICE_DATE), '01-01-1900'), 'DD-MM-YYYY') - 1) AND TRUNC(SYSDATE) AND TRIM(:P_INVOICE_DATE) IS NOT NULL)
                OR (ps.amount_due_original = TO_NUMBER(NVL(TRIM(:P_INVOICE_AMOUNT), '-999999999')) AND TRIM(:P_INVOICE_AMOUNT) IS NOT NULL)
            )
        )
    )
