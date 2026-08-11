#!/bin/bash

echo "Syncing development dependencies using uv..."
uv sync

echo ""
echo "Starting FastAPI server with hot-reload..."
uv run task start
