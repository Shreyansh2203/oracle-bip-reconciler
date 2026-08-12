import logging

import httpx
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger("reconciliation_api")

limiter = Limiter(key_func=get_remote_address)

def get_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client
