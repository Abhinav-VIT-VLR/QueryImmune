"""Print a short-lived local demo token; this module is never an API endpoint."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from queryimmune_db.api.config import Settings


def _segment(value: dict[str, object]) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def main() -> None:
    settings = Settings.from_env()
    if os.getenv("QUERYIMMUNE_DEMO_AUTH") != "1" or settings.ai_provider != "demo":
        raise SystemExit("Demo token issuance is available only in the explicitly enabled local demo container")
    secret = settings.auth_signing_secret
    if not secret or len(secret.encode("utf-8")) < 32:
        raise SystemExit("A local AUTH_JWT_HS256_SECRET of at least 32 bytes is required")
    header = _segment({"alg": "HS256", "typ": "JWT"})
    claims = _segment({"sub": "demo:dr-ada", "iss": settings.auth_issuer,
        "aud": settings.auth_audience, "iat": int(time.time()), "exp": int(time.time()) + 900})
    signed = f"{header}.{claims}"
    signature = hmac.new(secret.encode("utf-8"), signed.encode("ascii"), hashlib.sha256).digest()
    print(f"{signed}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}")


if __name__ == "__main__":
    main()
