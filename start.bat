@echo off
echo Syncing development dependencies using uv...
call uv sync

echo.
echo Starting FastAPI server with hot-reload...
call uv run task start
