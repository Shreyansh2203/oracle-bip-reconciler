import os
from urllib.parse import urlparse


def get_oracle_url():
    url = os.getenv("ORACLE_URL", "")
    if not url:
        return url

    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(f"ORACLE_URL must include a scheme (http:// or https://). Got: {url}")

    if url.startswith("http://"):
        try:
            parsed = urlparse(url)
            host = parsed.hostname
        except ValueError:
            host = None
        if host not in ["localhost", "127.0.0.1"]:
            raise ValueError(f"Insecure HTTP protocol is not allowed for non-localhost URLs: {url}")

    return url

