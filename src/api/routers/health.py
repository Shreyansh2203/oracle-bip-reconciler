from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from src.core.config import settings
from src.core.dependencies import get_api_key, limiter

router = APIRouter()

# Load HTML content at module initialization to avoid blocking I/O in async route
html_path = Path(__file__).parent.parent.parent / "templates" / "index.html"
HTML_CONTENT = html_path.read_text(encoding="utf-8") if html_path.exists() else "<h1>API is running</h1>"


@router.get("/", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def root(request: Request, api_key: str | None = Depends(get_api_key)) -> HTMLResponse:
    return HTMLResponse(content=HTML_CONTENT)


@router.get("/health")
@limiter.limit("60/minute")
async def health_check(request: Request) -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
@limiter.limit("60/minute")
async def readiness_check(request: Request) -> dict[str, str]:
    if not settings.ORACLE_USER or not settings.ORACLE_PASS or not settings.ORACLE_URL:
        raise HTTPException(status_code=503, detail="Service not ready: missing required configuration")
    return {"status": "ready"}
