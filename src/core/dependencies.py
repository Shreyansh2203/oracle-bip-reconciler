import logging

import httpx
from fastapi import HTTPException, Request
from slowapi import Limiter

from src.core.config import settings

logger = logging.getLogger("reconciliation_api")

# Returned verbatim, so nothing about the deployment leaks through the refusal.
PUBLIC_ACCESS_DISABLED = (
    "This service refuses unauthenticated requests. It must run behind an authenticating "
    "proxy, or ALLOW_UNAUTHENTICATED_ACCESS must be set to true to accept the risk."
)


def rate_limit_key(request: Request) -> str:
    """Identify the caller whose bucket the request spends.

    Uvicorn only rewrites ``request.client`` from ``X-Forwarded-For`` when it is started
    with ``--proxy-headers`` and a trusted ``--forwarded-allow-ips``, and this service's
    server configurations set neither. Behind Render's edge that leaves ``request.client.host``
    as the edge's own address, so every caller shares one 10/minute bucket.

    Honouring the header here is only safe when the thing in front is trusted to set it, so
    it is opt-in through TRUSTED_PROXY_HEADERS rather than assumed. The last entry is read,
    which is the one the edge appended: a caller who prepends a forged address to the list
    is ignored rather than believed.

    Deliberately not delegated to ``--forwarded-allow-ips=*``, which makes uvicorn trust the
    leftmost entry from any peer and so hands the rate limit to anyone willing to set a
    header.
    """
    peer = request.client.host if request.client else "unknown"
    if not settings.TRUSTED_PROXY_HEADERS:
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded.strip():
        return peer
    return forwarded.split(",")[-1].strip() or peer


limiter = Limiter(key_func=rate_limit_key)


def require_public_access_opt_in() -> None:
    """Fail closed until an operator explicitly accepts unauthenticated access.

    The reconciliation endpoint hands a caller the entire invoice and receipt ledger of
    whichever customer it names, and the Oracle service account is tenant-wide, so the
    endpoint is only safe behind something that authenticates. The service cannot
    authenticate the caller itself, so the decision is an environment variable that is
    off until it is deliberately turned on.
    """
    if not settings.ALLOW_UNAUTHENTICATED_ACCESS:
        logger.error(
            "Refusing /v1/reconcile/batch: ALLOW_UNAUTHENTICATED_ACCESS is not set. "
            "Put an authenticating proxy in front of this service, or set it to true."
        )
        raise HTTPException(status_code=503, detail=PUBLIC_ACCESS_DISABLED)


def get_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client
