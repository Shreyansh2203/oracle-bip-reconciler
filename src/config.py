from __future__ import annotations

import os
from urllib.parse import urlparse


def get_oracle_url() -> str:
    url = os.getenv("ORACLE_URL", "").strip()
    allow_insecure = os.getenv("ALLOW_INSECURE_ORACLE_HTTP", "false").strip().lower() == "true"
    if not url:
        raise ValueError(
            "ORACLE_URL environment variable is missing! Please set it in your hosting provider's dashboard."
        )

    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(f"ORACLE_URL must include a scheme (http:// or https://). Got: {url}")

    if url.startswith("http://"):
        try:
            parsed = urlparse(url)
            host = parsed.hostname
        except ValueError:
            host = None
        if not allow_insecure and host not in ["localhost", "127.0.0.1", "::1"]:
            raise ValueError(f"Insecure HTTP protocol is not allowed for non-localhost URLs: {url}")

    return url.rstrip("/")
