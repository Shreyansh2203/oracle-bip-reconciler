from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Declared before ORACLE_URL because validate_oracle_url reads it through
    # ValidationInfo.data, which only holds the fields validated so far.
    ALLOW_INSECURE_ORACLE_HTTP: bool = False

    ORACLE_URL: str
    ORACLE_USER: str
    # repr=False keeps the password out of str(settings), repr(settings) and the
    # "input_value" of any ValidationError, all of which can reach the logs.
    ORACLE_PASS: str = Field(repr=False)
    CORS_ORIGINS: str = ""
    REDIS_URL: str | None = None

    # ORACLE_USER/ORACLE_PASS are one tenant-wide BI Publisher service account, so anything
    # that can reach the reconciliation endpoint can read the whole ledger of every customer
    # that account can see, by naming a customer. Defaulting to False means a deploy that
    # forgot to decide is a deploy that serves nobody; the operator has to set this to true
    # once an authenticating proxy is in front of the service. See README.md#deployment.
    ALLOW_UNAUTHENTICATED_ACCESS: bool = False

    # Whether the reverse proxy in front of this service overwrites X-Forwarded-For. When it
    # does not, the rate limiter falls back to the socket peer, which behind a shared edge
    # is one address for every caller. Kept off by default because trusting the header
    # without checking who set it hands the rate limit to whoever chooses to set it.
    TRUSTED_PROXY_HEADERS: bool = False

    @field_validator("ORACLE_URL", mode="after")
    @classmethod
    def validate_oracle_url(cls, v: str, info) -> str:
        url = v.strip()
        if not url:
            raise ValueError("ORACLE_URL environment variable is missing!")

        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"ORACLE_URL must include a scheme (http:// or https://). Got: {url}")

        if url.startswith("http://"):
            try:
                parsed = urlparse(url)
                host = parsed.hostname
            except ValueError:
                host = None

            allow_insecure = info.data.get("ALLOW_INSECURE_ORACLE_HTTP", False)
            if not allow_insecure and host not in ["localhost", "127.0.0.1", "::1"]:
                raise ValueError(f"Insecure HTTP protocol is not allowed for non-localhost URLs: {url}")

        return url.rstrip("/")


settings = Settings()
