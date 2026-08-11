import asyncio
import logging
from typing import Any

import httpx

from src.constants import DEFAULT_CONCURRENCY
from src.models import ReconciliationRequest
from src.services.oracle_bip import fetch_bip_invoices, fetch_bip_receipts

logger = logging.getLogger("reconciliation_api.discovery")

def _is_data_row(row: dict[str, Any]) -> bool:
    """Check if a CSV row contains actual data columns (not just parameter echo)."""
    data_columns = {
        "BILL_CUSTOMER_NAME",
        "TRANSACTION_NUMBER",
        "RECEIPT_NUMBER",
        "CUSTOMER_NAME",
        "ACCOUNT_NUMBER",
        "BUSINESS_UNIT",
        "CURRENCY",
        "INVOICE_STATUS",
        "RECEIPT_STATUS_CODE",
    }
    row_keys = {k.lstrip("\ufeff").strip().upper() for k in row.keys()}
    return bool(row_keys & data_columns)


def _filter_data_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter out parameter-only rows from BIP CSV results."""
    if not rows:
        return rows
    # Check first row: if it has data columns, all rows are data rows
    if _is_data_row(rows[0]):
        return rows
    # Otherwise filter individually (shouldn't normally happen)
    return [r for r in rows if _is_data_row(r)]


async def _discover_by_receipt(client: httpx.AsyncClient, user: str, pwd: str, r_num: str) -> str | None:
    if not r_num:
        return None

    logger.info(f"Step 1: Searching Receipt Report using payment_reference '{r_num}'")

    def _verify_receipt(raw_receipts: list[dict[str, Any]]) -> str | None:
        if raw_receipts:
            cand_name = str(raw_receipts[0].get("BILL_CUSTOMER_NAME", "")).strip()
            if cand_name:
                return cand_name
        return None

    r_res = await fetch_bip_receipts(client, user, pwd, receipt_number=r_num)
    cand = _verify_receipt(_filter_data_rows(r_res))
    if cand:
        return cand

    return None

async def _discover_by_invoice_sequence(client: httpx.AsyncClient, user: str, pwd: str, invoices: list[Any]) -> str | None:
    levels = [
        {"desc": "Step 3 (Priority 1: Invoice Number)", "use_amt": False, "use_date": False},
        {"desc": "Step 3 (Priority 2: Invoice Number + Amount)", "use_amt": True, "use_date": False},
        {"desc": "Step 3 (Priority 3: Invoice Number + Amount + Date)", "use_amt": True, "use_date": True},
    ]

    for level in levels:
        logger.info(f"Executing {level['desc']} sequence...")

        queries = []
        for inv in invoices:
            i_num = str(inv.invoice_number).strip() if inv.invoice_number else ""
            if not i_num:
                continue

            kwargs = {"invoice_number": i_num}
            if level["use_amt"] and inv.invoice_amount is not None:
                kwargs["invoice_amount"] = str(inv.invoice_amount)
            if level["use_date"] and inv.invoice_date:
                kwargs["invoice_date"] = str(inv.invoice_date).strip()

            queries.append(kwargs)

        if not queries:
            continue

        sem = asyncio.Semaphore(DEFAULT_CONCURRENCY)
        discovered_candidates = set()

        async def _bounded_fetch(kw_args: dict[str, Any], current_sem: asyncio.Semaphore = sem) -> list[dict[str, Any]]:
            async with current_sem:
                return await fetch_bip_invoices(client, user, pwd, **kw_args)

        tasks = [asyncio.create_task(_bounded_fetch(kw)) for kw in queries]

        try:
            for coro in asyncio.as_completed(tasks):
                try:
                    i_res = await coro
                    invoices_raw = _filter_data_rows(i_res)
                    if invoices_raw:
                        d_name = str(invoices_raw[0].get("BILL_CUSTOMER_NAME", "")).strip()
                        if d_name:
                            discovered_candidates.add(d_name)
                            if len(discovered_candidates) > 1:
                                break  # Short-circuit only if we found a conflict
                except Exception as e:
                    logger.error(f"Invoice fetch failed in sequence: {e}")
        finally:
            for t in tasks:
                t.cancel()

        if len(discovered_candidates) > 1:
            break  # Short-circuit: stop processing if we found a conflict

        if len(discovered_candidates) == 1:
            d_name = list(discovered_candidates)[0]
            logger.info(f"Successfully isolated unique customer '{d_name}' at {level['desc']}")
            return d_name
        elif len(discovered_candidates) > 1:
            logger.warning(f"Multiple customers found {list(discovered_candidates)} at {level['desc']}, narrowing down...")
        else:
            logger.warning(f"No customers found at {level['desc']}")

    return None

async def discover_potential_customers(
    client: httpx.AsyncClient, user: str, pwd: str, payload: ReconciliationRequest
) -> tuple[str | None, list[dict[str, Any]] | None]:
    c_name = str(payload.customer_name).strip() if payload.customer_name else None
    r_num = str(payload.payment_reference).strip() if payload.payment_reference else None

    # Special Case: Both Null -> Skip directly to Step 3
    if not c_name and not r_num:
        logger.warning("Special Case Triggered: Both Customer Name and Payment Reference are NULL. Jumping to Step 3.")
        d_name = await _discover_by_invoice_sequence(client, user, pwd, payload.invoices)
        return d_name, None

    # Step 1: Reference-Based Identification (Payment Reference)
    if r_num:
        d_name = await _discover_by_receipt(client, user, pwd, r_num)
        if d_name:
            logger.info(f"Step 1: Successfully identified customer '{d_name}' via Payment Reference.")
            return d_name, None
        logger.warning("Step 1 failed to identify customer. Moving to Step 2.")

    # Step 2: Direct Identification (Customer Name)
    if c_name:
        logger.info(f"Step 2: Testing customer_name from JSON: '{c_name}' in Receipt Details Report")
        r_res = await fetch_bip_receipts(client, user, pwd, customer_name=c_name)

        if _filter_data_rows(r_res):
            logger.info(f"Step 2: Confirmed customer '{c_name}' has ledger data.")
            return c_name, r_res

        logger.warning(f"Step 2: Customer '{c_name}' has no ledger data. Moving to Step 3.")

    # Step 3: Invoice-Based Identification
    logger.info("Step 3: Attempting to identify Customer Name using Invoice Details Report sequence...")
    d_name = await _discover_by_invoice_sequence(client, user, pwd, payload.invoices)
    if d_name:
        return d_name, None

    logger.warning("Customer could not be identified after all steps. Returning NULL.")
    return None, None
