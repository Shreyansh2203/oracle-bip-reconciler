import asyncio
import logging
from collections.abc import Iterable, Iterator
from typing import Any

import httpx

from src.constants import BIP_MAX_DISCOVERY_INVOICES, DEFAULT_CONCURRENCY
from src.models import ReconciliationRequest
from src.services.oracle_bip import fetch_bip_invoices, fetch_bip_receipts

logger = logging.getLogger("reconciliation_api.discovery")


class CustomerDiscoveryLimitError(ValueError):
    pass


def _customer_name(row: dict[str, Any]) -> str:
    return str(row.get("BILL_CUSTOMER_NAME") or row.get("CUSTOMER_NAME") or "").strip()


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

    logger.info("Step 1: Searching Receipt Report by payment reference")

    r_res = await fetch_bip_receipts(client, user, pwd, receipt_number=r_num)
    names = {
        _customer_name(row)
        for row in _filter_data_rows(r_res)
        if len(r_num) >= 5
        and len(str(row.get("RECEIPT_NUMBER") or "").strip()) >= 5
        and (
            r_num.casefold() in str(row.get("RECEIPT_NUMBER") or "").strip().casefold()
            or str(row.get("RECEIPT_NUMBER") or "").strip().casefold() in r_num.casefold()
        )
    }
    names.discard("")
    if len(names) == 1:
        return names.pop()
    if len(names) > 1:
        logger.warning("Receipt reference matched more than one customer; refusing to choose.")
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
        if len(queries) > BIP_MAX_DISCOVERY_INVOICES:
            raise CustomerDiscoveryLimitError(
                f"Invoice-only customer discovery is limited to {BIP_MAX_DISCOVERY_INVOICES} lines. "
                "Provide a customer_name or payment_reference for larger batches."
            )

        sem = asyncio.Semaphore(DEFAULT_CONCURRENCY)
        discovered_candidates: set[str] = set()

        async def _bounded_fetch(kw_args: dict[str, Any], current_sem: asyncio.Semaphore = sem) -> list[dict[str, Any]]:
            async with current_sem:
                return await fetch_bip_invoices(client, user, pwd, **kw_args)

        import itertools

        def chunked_iterable(iterable: Iterable[dict[str, Any]], size: int) -> Iterator[tuple[dict[str, Any], ...]]:
            it = iter(iterable)
            while True:
                chunk = tuple(itertools.islice(it, size))
                if not chunk:
                    break
                yield chunk

        for query_chunk in chunked_iterable(queries, DEFAULT_CONCURRENCY):
            tasks = [asyncio.create_task(_bounded_fetch(kw)) for kw in query_chunk]
            try:
                for coro in asyncio.as_completed(tasks):
                    i_res = await coro
                    invoices_raw = _filter_data_rows(i_res)
                    if invoices_raw:
                        discovered_candidates.update(
                            name for row in invoices_raw if (name := _customer_name(row))
                        )
                        if len(discovered_candidates) > 1:
                            break
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            if len(discovered_candidates) > 1:
                break

        if len(discovered_candidates) == 1:
            d_name = list(discovered_candidates)[0]
            logger.info("Successfully isolated one customer at %s", level["desc"])
            return d_name
        elif len(discovered_candidates) > 1:
            logger.warning("Multiple customers found at %s; narrowing down.", level["desc"])
            continue
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
            logger.info("Step 1: Identified customer via payment reference.")
            return d_name, None
        logger.warning("Step 1 failed to identify customer. Moving to Step 2.")

    # Step 2: Direct Identification (Customer Name)
    if c_name:
        logger.info("Step 2: Testing supplied customer name in Receipt Details Report")
        r_res = await fetch_bip_receipts(client, user, pwd, customer_name=c_name)
        data_rows = _filter_data_rows(r_res)
        matching_rows = [row for row in data_rows if _customer_name(row).casefold() == c_name.casefold()]
        found_names = {_customer_name(row).casefold() for row in data_rows if _customer_name(row)}
        if matching_rows and found_names == {c_name.casefold()}:
            logger.info("Step 2: Confirmed supplied customer has ledger data.")
            return c_name, matching_rows

        logger.warning("Step 2: Supplied customer was not uniquely confirmed. Moving to Step 3.")

    # Step 3: Invoice-Based Identification
    logger.info("Step 3: Attempting to identify Customer Name using Invoice Details Report sequence...")
    d_name = await _discover_by_invoice_sequence(client, user, pwd, payload.invoices)
    if d_name:
        return d_name, None

    logger.warning("Customer could not be identified after all steps. Returning NULL.")
    return None, None
