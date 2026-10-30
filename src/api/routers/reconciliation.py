import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from src.core.config import settings
from src.core.dependencies import get_client, limiter, require_public_access_opt_in
from src.models import ReconciliationRequest
from src.services.reconciliation import process_reconciliation_batch

router = APIRouter()

@router.post("/v1/reconcile/batch", response_model=ReconciliationRequest | None)
@limiter.limit("10/minute")
async def reconcile_data_batch(
    request: Request,
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
        oracle_pass=settings.ORACLE_PASS
    )
    if err:
        raise HTTPException(status_code=status or 500, detail=err)
    return res
