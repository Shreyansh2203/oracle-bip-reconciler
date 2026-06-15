-- Optimized Receipt Details Query for Oracle BIP
-- Improvements:
-- 1. Outer query wrapper cleanly evaluates final math based on exactly ONE subquery
-- 2. Eliminated duplicate correlated subqueries that were destroying performance
-- 3. Removed leading wildcards to enforce exact matching for receipt numbers
-- 4. Math happens on parameters, not table columns, allowing index usage

SELECT 
    bill_customer_name,
    bill_customer_number,
    business_unit,
    receipt_method,
    receipt_number,
    receipt_date,
    application_type,
    receipt_amount,
    applied_amount,
    (receipt_amount - applied_amount) AS unapplied_amount,
    currency,
    receipt_status_code
FROM (
    SELECT
        hp.party_name                           AS bill_customer_name,
        hca.account_number                      AS bill_customer_number,
        hou.name                                AS business_unit,
        arm.name                                AS receipt_method,
        acr.receipt_number                      AS receipt_number,
        TO_CHAR(acr.receipt_date,\'DD-MM-YYYY\')  AS receipt_date,
        \'\'										AS application_type,
        acr.amount                              AS receipt_amount,
        
        NVL((
            SELECT SUM(AMOUNT_APPLIED)
            FROM ar_receivable_applications_all araa
            WHERE UPPER(NVL(araa.display,\'Y\')) = \'Y\'
            AND araa.CASH_RECEIPT_ID = acr.CASH_RECEIPT_ID
        ), 0)                                   AS applied_amount,
        
        acr.currency_code                       AS currency,
        acr.status								AS receipt_status_code
        
    FROM
        ar_cash_receipts_all            acr,
        ar_receipt_methods              arm,
        hz_cust_accounts                hca,
        hz_parties                      hp,
        hr_organization_units           hou

    WHERE 1 = 1
        AND acr.pay_from_customer       = hca.cust_account_id(+)
        AND hca.party_id               	= hp.party_id(+)
        AND acr.receipt_method_id      	= arm.receipt_method_id(+)
        AND acr.org_id                  = hou.organization_id(+)

        AND (
            (   acr.status IN (\'UNID\',\'UNAPP\')
                AND COALESCE(:P_RECEIPT_NUMBER, :P_CUSTOMER_NAME, :P_RECEIPT_DATE, :P_RECEIPT_AMOUNT) IS NULL
            )
            OR ( 
                acr.status IN (\'APP\')
                AND ( 
                    (hp.party_name = :P_CUSTOMER_NAME AND :P_CUSTOMER_NAME IS NOT NULL)
                    OR (
                        (acr.receipt_date >= TO_DATE(:P_RECEIPT_DATE, \'DD-MM-YYYY\') - 3)
                        AND (acr.receipt_date < TO_DATE(:P_RECEIPT_DATE, \'DD-MM-YYYY\') + 4)
                        AND :P_RECEIPT_DATE IS NOT NULL
                    )
                    OR (acr.amount = :P_RECEIPT_AMOUNT AND :P_RECEIPT_AMOUNT IS NOT NULL)
                    OR (acr.receipt_number = :P_RECEIPT_NUMBER AND :P_RECEIPT_NUMBER IS NOT NULL)
                )
            )
        )
)
