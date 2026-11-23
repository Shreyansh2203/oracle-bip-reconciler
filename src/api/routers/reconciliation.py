from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.core.config import settings
from src.core.dependencies import get_client, limiter, require_public_access_opt_in
from src.core.metrics import RECONCILIATION_COUNT
from src.models import ErrorResponse, ReconciliationRequest
from src.services.reconciliation import process_reconciliation_batch

router = APIRouter(tags=["reconciliation"])

RECONCILIATION_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "model": ReconciliationRequest | None,
        "description": (
            "Successful reconciliation result containing matched Oracle Fusion fields, "
            "or null if the customer could not be resolved from the ledger."
        ),
    },
    413: {
        "model": ErrorResponse,
        "description": "Request body exceeds the maximum permitted size limit (2 MiB).",
    },
    422: {
        "model": ErrorResponse,
        "description": (
            "Unprocessable request due to validation failure or exceeding the maximum allowed "
            "invoice lines for customer discovery."
        ),
    },
    429: {
        "model": ErrorResponse,
        "description": "Too many requests. Rate limit is 10 requests per minute.",
    },
    502: {
        "model": ErrorResponse,
        "description": "Bad Gateway. Upstream Oracle ERP Cloud / BI Publisher service is unavailable.",
    },
    503: {
        "model": ErrorResponse,
        "description": "Service Unavailable. Unauthenticated access is disabled; authenticating proxy required.",
    },
}


@router.post(
    "/v1/reconcile/batch",
    response_model=ReconciliationRequest | None,
    responses=RECONCILIATION_RESPONSES,
    summary="Reconcile a batch of invoices against Oracle receipts",
    description="Reconcile customer invoices against Oracle ERP Cloud receipts using tiered matching.",
    operation_id="reconcile_data_batch",
)
@router.post(
    "/v1/reconciliations",
    response_model=ReconciliationRequest | None,
    responses=RECONCILIATION_RESPONSES,
    summary="Reconcile a batch of invoices against Oracle receipts (RESTful resource alias)",
    description="RESTful resource alias to reconcile customer invoices against Oracle ERP Cloud receipts.",
    operation_id="reconcile_data_batch_rest",
)
@limiter.limit("10/minute")
async def reconcile_data_batch(
    request: Request,
    response: Response,
    payload: ReconciliationRequest,
    client: httpx.AsyncClient = Depends(get_client),  # noqa: B008
    _: None = Depends(require_public_access_opt_in),  # noqa: B008
) -> ReconciliationRequest | None:
    payload = payload.model_copy(
        update={
            "fusion_customer_name": None,
            "fusion_receipt_number": None,
            "fusion_receipt_date": None,
            "fusion_customer_number": None,
            "fusion_currency": None,
            "fusion_receipt_status_code": None,
            "fusion_applied_amount": None,
            "match_phase": None,
            "match_rule": None,
            "invoices": [
                invoice.model_copy(
                    update={
                        "fusion_invoice_number": None,
                        "fusion_invoice_date": None,
                        "fusion_invoice_amount": None,
                        "match_phase": None,
                        "match_rule": None,
                    }
                )
                for invoice in payload.invoices
            ],
        }
    )
    res, err, status = await process_reconciliation_batch(
        payload=payload,
        client=client,
        oracle_user=settings.ORACLE_USER,
        oracle_pass=settings.ORACLE_PASS,
        request_id=getattr(request.state, "request_id", None),
    )
    RECONCILIATION_COUNT.labels(status="error" if err else "success").inc()
    if err:
        raise HTTPException(status_code=status or 500, detail=err)
    return res
