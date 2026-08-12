import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from src.core.config import settings
from src.core.dependencies import get_client, limiter
from src.models import ReconciliationRequest
from src.services.reconciliation import process_reconciliation_batch

router = APIRouter()

@router.post("/v1/reconcile/batch", response_model=ReconciliationRequest | None)
@limiter.limit("10/minute")
async def reconcile_data_batch(
    request: Request,
    payload: ReconciliationRequest,
    client: httpx.AsyncClient = Depends(get_client),  # noqa: B008
) -> ReconciliationRequest | None:
    res, err, status = await process_reconciliation_batch(
        payload=payload,
        client=client,
        oracle_user=settings.ORACLE_USER,
        oracle_pass=settings.ORACLE_PASS
    )
    if err:
        raise HTTPException(status_code=status or 500, detail=err)
    return res
