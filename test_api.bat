@echo off
echo Running JSON payload test script...
set PYTHONPATH=.
uv run python scripts/test_jsons.py
pause
