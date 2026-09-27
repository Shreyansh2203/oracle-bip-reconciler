# syntax=docker/dockerfile:1

# ── builder ──────────────────────────────────────────────────────────────────────────────────
# Kept only so the wheel-building toolchain and the uv cache never reach the runtime image.
# The virtualenv is built here and copied into the runtime stage whole: both stages are the
# same base image at the same paths, so the compiled extension modules in it stay valid.
FROM python:3.13.7-slim-bookworm@sha256:adafcc17694d715c905b4c7bebd96907a1fd5cf183395f0ebc4d3428bd22d92d AS builder

# Use the interpreter that is already in the base image rather than letting uv download and
# manage one; copy rather than hardlink, because the cache and the venv do not share a
# filesystem in every builder.
ENV UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# uv pinned by digest, not by `pip install uv`, which resolved to whatever was newest on the
# day the image happened to be built.
COPY --from=ghcr.io/astral-sh/uv:0.12.14@sha256:1946145b8706ad9e5c0e79a513f9e324b58d5e38126bb2c8b7dbfca61febeb45 /uv /uvx /bin/

# Manifests only, so editing a source file does not invalidate the dependency layer.
# --no-install-project: the app is imported from /app (PYTHONPATH below), never installed.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache

# ── runtime ─────────────────────────────────────────────────────────────────────────────────
FROM python:3.13.7-slim-bookworm@sha256:adafcc17694d715c905b4c7bebd96907a1fd5cf183395f0ebc4d3428bd22d92d

# Unbuffered stdout so a log line reaches `docker logs` when it is written, and no .pyc
# files, so the read-only source tree never needs to be written to. PYTHONPATH is set
# explicitly rather than relied on through the working directory, so the uvicorn workers
# import src.main whichever directory the image is started from.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:/usr/local/bin:${PATH}"

WORKDIR /app

# A fixed uid/gid rather than a bare `useradd`, so a bind-mounted volume has a predictable
# owner on the host and the process is not root inside the container.
RUN groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin appuser

COPY --from=builder /app/.venv /app/.venv
COPY --chown=10001:10001 src ./src
COPY --chown=10001:10001 api ./api

USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM

# Hits the real GET /health endpoint, in exec form so there is no shell to inject through and
# no curl to install: urllib is in the standard library. /health is liveness only and does no
# I/O, which is what a healthcheck wants -- /ready additionally depends on Oracle
# credentials being configured, and an operator who has not set them yet should see a
# running container, not an unhealthy one.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"]

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
