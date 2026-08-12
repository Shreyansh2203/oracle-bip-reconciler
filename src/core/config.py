from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ORACLE_URL: str
    ORACLE_USER: str
    ORACLE_PASS: str
    CORS_ORIGINS: str = ""
    ALLOW_INSECURE_ORACLE_HTTP: bool = False
    REDIS_URL: str | None = None

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
