from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from queryimmune_db import env as _env  # noqa: F401


@dataclass(frozen=True)
class Settings:
    auth_signing_secret: str | None = field(repr=False)
    auth_issuer: str
    auth_audience: str
    log_level: str
    result_limit: int
    review_limit: int
    ai_provider: str
    ai_base_url: str | None
    ai_api_key: str | None = field(repr=False)
    ai_model: str | None
    ai_timeout_seconds: float
    ai_max_response_bytes: int

    @classmethod
    def from_env(cls) -> "Settings":
        result_limit = int(os.getenv("QUERYIMMUNE_RESULT_LIMIT", "100"))
        review_limit = int(os.getenv("QUERYIMMUNE_REVIEW_LIMIT", "25"))
        if not 1 <= review_limit <= result_limit <= 500:
            raise ValueError("require 1 <= QUERYIMMUNE_REVIEW_LIMIT <= QUERYIMMUNE_RESULT_LIMIT <= 500")
        ai_provider = os.getenv("AI_PROVIDER", "disabled").lower()
        if ai_provider not in {"disabled", "openai-compatible", "demo"}:
            raise ValueError("AI_PROVIDER must be disabled, demo, or openai-compatible")
        ai_timeout = float(os.getenv("AI_TIMEOUT_SECONDS", "20"))
        ai_max_response = int(os.getenv("AI_MAX_RESPONSE_BYTES", "16384"))
        if not 1 <= ai_timeout <= 60 or not 1024 <= ai_max_response <= 131072:
            raise ValueError("AI timeout or response size is outside the supported range")
        base_url = os.getenv("AI_BASE_URL")
        if base_url:
            parsed = urlsplit(base_url)
            loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if parsed.scheme not in ({"https", "http"} if loopback else {"https"}) or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("AI_BASE_URL must be HTTPS (HTTP is allowed only for loopback) and contain no user info")
        return cls(
            auth_signing_secret=os.getenv("AUTH_JWT_HS256_SECRET"),
            auth_issuer=os.getenv("AUTH_JWT_ISSUER", "queryimmune-auth"),
            auth_audience=os.getenv("AUTH_JWT_AUDIENCE", "queryimmune-api"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            result_limit=result_limit,
            review_limit=review_limit,
            ai_provider=ai_provider,
            ai_base_url=base_url.rstrip("/") if base_url else None,
            ai_api_key=os.getenv("AI_API_KEY"),
            ai_model=os.getenv("AI_MODEL"),
            ai_timeout_seconds=ai_timeout,
            ai_max_response_bytes=ai_max_response,
        )
