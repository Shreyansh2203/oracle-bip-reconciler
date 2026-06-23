FROM python:3.12-slim

# Prevent python from writing pyc files and buffering stdout
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Copy dependency manifests
COPY pyproject.toml uv.lock ./

# Install dependencies efficiently without project source code
RUN uv sync --no-dev --frozen

# Copy project source code
COPY . .

# Expose the standard FastAPI port
EXPOSE 8000

# Start the uvicorn server
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
