import logging

import httpx
from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.core.config import settings

logger = logging.getLogger("reconciliation_api")

limiter = Limiter(key_func=get_remote_address)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def get_api_key(api_key: str | None = Security(api_key_header)) -> str | None:
    expected_api_key = settings.API_KEY
    if not expected_api_key:
        logger.warning("API_KEY environment variable is not set. API is unsecured.")
        return api_key
    if not api_key or api_key != expected_api_key:
        raise HTTPException(status_code=403, detail="Could not validate API Key")
    return api_key

def get_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client
