from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import time
from collections import deque
from threading import Lock
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
import psycopg

from queryimmune_db.api.config import Settings
from queryimmune_db.domain.identity import Actor
from queryimmune_db.repositories.healthcare import HealthcareRepository

bearer = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
PASSWORD_HASH_ITERATIONS = 600_000
ACCESS_TOKEN_TTL_SECONDS = 900
_login_failures: dict[str, deque[float]] = {}
_login_failure_lock = Lock()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


def hash_login_password(password: str) -> str:
    if len(password.encode("utf-8")) > 1024:
        raise ValueError("password is too long")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS
    )
    salt_text = base64.urlsafe_b64encode(salt).rstrip(b"=").decode("ascii")
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt_text}${digest.hex()}"


def verify_login_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) != PASSWORD_HASH_ITERATIONS:
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = bytes.fromhex(digest_text)
        if len(salt) != 16 or len(expected) != 32 or len(password.encode("utf-8")) > 1024:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def issue_access_token(subject: str, settings: Settings, *, now: int | None = None) -> str:
    secret = settings.auth_signing_secret
    if not secret or len(secret.encode("utf-8")) < 32:
        raise RuntimeError("authentication issuer is not configured")
    issued_at = int(time.time()) if now is None else now
    claims = {
        "sub": subject,
        "iss": settings.auth_issuer,
        "aud": settings.auth_audience,
        "iat": issued_at,
        "exp": issued_at + ACCESS_TOKEN_TTL_SECONDS,
    }

    def encode(value: object) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header = encode({"alg": "HS256", "typ": "JWT"})
    payload = encode(claims)
    signed = f"{header}.{payload}"
    signature = hmac.new(secret.encode("utf-8"), signed.encode("ascii"), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{signed}.{encoded_signature}"


def _is_login_throttled(client: str, *, now: float | None = None) -> bool:
    current = time.monotonic() if now is None else now
    cutoff = current - 900
    with _login_failure_lock:
        attempts = _login_failures.setdefault(client, deque())
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if len(attempts) >= 5:
            return True
        attempts.append(current)
        return False


def _clear_login_failures(client: str) -> None:
    with _login_failure_lock:
        _login_failures.pop(client, None)


@router.post("/login")
def login(body: LoginRequest, request: Request) -> dict[str, object]:
    username = os.environ.get("AUTH_LOGIN_USERNAME", "")
    password_hash = os.environ.get("AUTH_LOGIN_PASSWORD_HASH", "")
    subject = os.environ.get("AUTH_LOGIN_SUBJECT", "")
    settings = Settings.from_env()
    if (
        not username
        or not password_hash
        or not subject
        or not settings.auth_signing_secret
        or len(settings.auth_signing_secret.encode("utf-8")) < 32
    ):
        raise HTTPException(status_code=503, detail="Login is not configured")

    client = request.client.host if request.client else "unknown"
    if _is_login_throttled(client):
        raise HTTPException(status_code=429, detail="Too many login attempts; try again later")

    username_matches = hmac.compare_digest(body.username.encode("utf-8"), username.encode("utf-8"))
    password_matches = verify_login_password(body.password, password_hash)
    if not username_matches or not password_matches:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    _clear_login_failures(client)
    try:
        actor = HealthcareRepository().actor_by_subject(subject)
    except (psycopg.Error, RuntimeError):
        raise HTTPException(status_code=503, detail="Identity service is unavailable") from None
    if actor is None or not actor.roles:
        raise HTTPException(status_code=403, detail="Configured login identity is not active")

    return {
        "access_token": issue_access_token(actor.subject, settings),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
    }


class TokenAuthenticator(Protocol):
    def subject_from_token(self, token: str) -> str: ...


def _decode_segment(segment: str) -> bytes:
    if not segment or re.fullmatch(r"[A-Za-z0-9_-]+", segment) is None:
        raise ValueError("invalid token encoding")
    padded = segment + "=" * (-len(segment) % 4)
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid token encoding") from exc


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON claim")
        result[key] = value
    return result


def _numeric_date(claims: dict[str, object], key: str, *, required: bool = False) -> float | None:
    value = claims.get(key)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {key} claim")
    try:
        numeric_value = float(value)
    except OverflowError as exc:
        raise ValueError(f"invalid {key} claim") from exc
    if not math.isfinite(numeric_value):
        raise ValueError(f"invalid {key} claim")
    return numeric_value


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
            header = json.loads(_decode_segment(header_part), object_pairs_hook=_unique_object)
            claims = json.loads(_decode_segment(claims_part), object_pairs_hook=_unique_object)
            if not isinstance(header, dict) or not isinstance(claims, dict):
                raise ValueError("invalid token structure")
            if header.get("alg") != "HS256" or header.get("typ", "JWT") != "JWT":
                raise ValueError("unsupported token algorithm")
            signed = f"{header_part}.{claims_part}".encode("ascii")
            expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _decode_segment(signature_part)):
                raise ValueError("invalid signature")
            now = int(time.time())
            expiration = _numeric_date(claims, "exp", required=True)
            if expiration is None or expiration <= now:
                raise ValueError("expired token")
            if claims.get("iss") != self.settings.auth_issuer:
                raise ValueError("invalid issuer")
            audience = claims.get("aud")
            if isinstance(audience, str):
                audiences = [audience]
            elif isinstance(audience, list) and audience and all(isinstance(item, str) and item for item in audience):
                audiences = audience
            else:
                raise ValueError("invalid audience")
            if self.settings.auth_audience not in audiences:
                raise ValueError("invalid audience")
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject or len(subject) > 255:
                raise ValueError("invalid subject")
            not_before = _numeric_date(claims, "nbf")
            if not_before is not None and not_before > now:
                raise ValueError("token not yet valid")
            issued_at = _numeric_date(claims, "iat")
            if issued_at is not None and issued_at > now:
                raise ValueError("token issued in the future")
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


@router.get("/me")
def current_user(actor: Actor = Depends(get_current_actor)) -> dict[str, object]:
    return {
        "subject": actor.subject,
        "display_name": actor.display_name,
        "roles": sorted(actor.roles),
    }
