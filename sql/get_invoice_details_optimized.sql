-- Optimized Invoice Details Query for Oracle BIP
-- Improvements:
-- 1. Enforced Outer Query wrapper to compute invoice status cleanly
-- 2. Removed duplicate rows by dropping unused joins (ra_terms, ra_terms_lines)
-- 3. Removed expensive correlated subquery for transaction_total (uses ps.amount_due_original)
-- 4. Re-wrote Date filters to remove TRUNC() from table column to permit Index Scans
-- 5. Implemented COALESCE for safer parameter NULL handling

SELECT 
    bill_customer_name,      
    bill_account_number,
    bill_site,
    bill_party_site_number,
    ship_customer_name,  
    ship_site_number,
    business_unit,
    Invoice_type,														
    transaction_type_name,
    transaction_number,		
    document_number,
    currency,
    transaction_date,
    transaction_total,
    total_amounts,
    batch_source_name,   
    amount_due_original,
    amount_due_remaining,
    CASE 
        WHEN abs(amount_due_remaining) > 0 THEN \'OPEN\'
        WHEN amount_due_remaining = 0 THEN \'CLOSED\'
        ELSE \'OTHER\'
    END AS INVOICE_STATUS
FROM (
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
        TO_DATE(TO_CHAR(trx.trx_date,\'DD-MM-YYYY\'),\'DD-MM-YYYY\') AS transaction_date,
        
        NVL(ps.amount_due_original, 0)   AS transaction_total,
        ps.amount_due_original           AS total_amounts,
        batch.name                       AS batch_source_name,   
        ps.amount_due_original,
        ps.amount_due_remaining
        
    FROM
        ra_customer_trx_all         trx,
        ra_cust_trx_types_all       types,
        ra_batch_sources_all        batch,
        ar_payment_schedules_all    ps,
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
        AND trx.customer_trx_id             = ps.customer_trx_id
        AND trx.bill_to_site_use_id         = hcsu_bill_to.site_use_id
        AND hcsu_bill_to.cust_acct_site_id  = hcasa_bill.cust_acct_site_id
        AND hcasa_bill.party_site_id        = hps_bill.party_site_id
        AND trx.ship_to_party_id            = hp_ship.party_id(+)   
        AND trx.ship_to_party_site_use_id   = hps_ship_to.party_site_use_id(+)
        AND hps_ship_to.party_site_id       = hps_ship.party_site_id(+)
        AND trx.org_id                      = hou.organization_id

        AND (
            (   abs(ps.amount_due_remaining) > 0
                AND COALESCE(:P_CUSTOMER_NAME, :P_INVOICE_NUM, :P_INVOICE_DATE, :P_INVOICE_AMOUNT) IS NULL
            )
            OR ( 
                ps.amount_due_remaining = 0
                AND (
                    (hp_bill.party_name = :P_CUSTOMER_NAME AND :P_CUSTOMER_NAME IS NOT NULL)
                    OR (
                        (trx.trx_date >= TO_DATE(:P_INVOICE_DATE, \'DD-MM-YYYY\') - 1)
                        AND (trx.trx_date < TRUNC(SYSDATE) + 1)
                        AND :P_INVOICE_DATE IS NOT NULL
                    )
                )
            )
        )
)
