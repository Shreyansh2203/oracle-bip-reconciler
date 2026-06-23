Write-Host "Syncing development dependencies using uv..." -ForegroundColor Cyan
uv sync

Write-Host "`nStarting FastAPI server with hot-reload..." -ForegroundColor Green
uv run task start
