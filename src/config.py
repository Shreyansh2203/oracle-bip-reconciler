import os


def get_oracle_url():
    url = os.getenv("ORACLE_URL", "")
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(f"ORACLE_URL must include a scheme (http:// or https://). Got: {url}")
    return url
