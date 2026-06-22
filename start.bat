@echo off
setlocal
color 0B

echo ===================================================
echo      ORACLE RECONCILIATION API - STARTUP
echo ===================================================
echo.
echo [1/2] Running Unit Tests (Pytest)...
echo.
uv run task test

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Tests failed! The server will not start.
    echo Please fix the failing tests before running this script again.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [OK] All tests passed!
echo.
echo [2/2] Starting Server (Uvicorn API)...
echo.
uv run task start

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Server crashed or failed to start.
    pause
    exit /b %ERRORLEVEL%
)
