from __future__ import annotations

import os
from dataclasses import dataclass

from queryimmune_db import env as _env  # noqa: F401


@dataclass(frozen=True)
class Settings:
    auth_signing_secret: str | None
    auth_issuer: str
    auth_audience: str
    log_level: str
    result_limit: int
    review_limit: int

    @classmethod
    def from_env(cls) -> "Settings":
        result_limit = int(os.getenv("QUERYIMMUNE_RESULT_LIMIT", "100"))
        review_limit = int(os.getenv("QUERYIMMUNE_REVIEW_LIMIT", "25"))
        if not 1 <= review_limit <= result_limit <= 500:
            raise ValueError("require 1 <= QUERYIMMUNE_REVIEW_LIMIT <= QUERYIMMUNE_RESULT_LIMIT <= 500")
        return cls(
            auth_signing_secret=os.getenv("AUTH_JWT_HS256_SECRET"),
            auth_issuer=os.getenv("AUTH_JWT_ISSUER", "queryimmune-auth"),
            auth_audience=os.getenv("AUTH_JWT_AUDIENCE", "queryimmune-api"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            result_limit=result_limit,
            review_limit=review_limit,
        )
