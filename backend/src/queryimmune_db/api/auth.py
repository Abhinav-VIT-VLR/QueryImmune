from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import time
from typing import Protocol

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import psycopg

from queryimmune_db.api.config import Settings
from queryimmune_db.domain.identity import Actor
from queryimmune_db.repositories.healthcare import HealthcareRepository

bearer = HTTPBearer(auto_error=False)


class TokenAuthenticator(Protocol):
    def subject_from_token(self, token: str) -> str: ...


def _decode_segment(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


class Hs256Authenticator:
    """JWT verifier only; issuance and key custody belong to the upstream identity provider."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def subject_from_token(self, token: str) -> str:
        secret = self.settings.auth_signing_secret
        if not secret or len(secret.encode("utf-8")) < 32:
            raise RuntimeError("authentication verifier is not configured")
        if len(token) > 8192:
            raise ValueError("invalid token")
        try:
            header_part, claims_part, signature_part = token.split(".")
            header = json.loads(_decode_segment(header_part))
            claims = json.loads(_decode_segment(claims_part))
            if not isinstance(header, dict) or not isinstance(claims, dict):
                raise ValueError("invalid token structure")
            if header.get("alg") != "HS256" or header.get("typ", "JWT") != "JWT":
                raise ValueError("unsupported token algorithm")
            signed = f"{header_part}.{claims_part}".encode("ascii")
            expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _decode_segment(signature_part)):
                raise ValueError("invalid signature")
            now = int(time.time())
            if not isinstance(claims.get("exp"), (int, float)) or not math.isfinite(claims["exp"]) or claims["exp"] <= now:
                raise ValueError("expired token")
            if claims.get("iss") != self.settings.auth_issuer:
                raise ValueError("invalid issuer")
            audience = claims.get("aud")
            if self.settings.auth_audience not in (audience if isinstance(audience, list) else [audience]):
                raise ValueError("invalid audience")
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject or len(subject) > 255:
                raise ValueError("invalid subject")
            if "nbf" in claims and (not isinstance(claims["nbf"], (int, float)) or not math.isfinite(claims["nbf"]) or claims["nbf"] > now):
                raise ValueError("token not yet valid")
            return subject
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("invalid bearer token") from exc


def get_current_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Actor:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"})
    settings = Settings.from_env()
    try:
        subject = Hs256Authenticator(settings).subject_from_token(credentials.credentials)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Authentication is unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token", headers={"WWW-Authenticate": "Bearer"}) from exc
    try:
        actor = HealthcareRepository().actor_by_subject(subject)
    except (psycopg.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Identity service is unavailable") from exc
    if actor is None:
        raise HTTPException(status_code=403, detail="User is not provisioned")
    if not actor.roles:
        raise HTTPException(status_code=403, detail="User has no active role")
    return actor
