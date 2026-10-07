"""Create local Compose secrets without overwriting an existing environment file."""
from __future__ import annotations

import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / ".env"


def main() -> None:
    if DESTINATION.exists():
        raise SystemExit(".env already exists; it was left unchanged")
    values = {
        "POSTGRES_PASSWORD": secrets.token_hex(32),
        "DATABASE_RUNTIME_PASSWORD": secrets.token_hex(32),
        "AUTH_JWT_HS256_SECRET": secrets.token_hex(32),
        "AUTH_JWT_ISSUER": "queryimmune-auth",
        "AUTH_JWT_AUDIENCE": "queryimmune-api",
        "AI_PROVIDER": "demo",
        "AI_BASE_URL": "",
        "AI_API_KEY": "",
        "AI_MODEL": "",
        "AI_TIMEOUT_SECONDS": "20",
        "AI_MAX_RESPONSE_BYTES": "16384",
        "QUERYIMMUNE_RESULT_LIMIT": "100",
        "QUERYIMMUNE_REVIEW_LIMIT": "25",
        "LOG_LEVEL": "INFO",
    }
    content = "# Local development secrets. Keep this file out of version control.\n"
    content += "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    DESTINATION.write_text(content, encoding="utf-8")
    print("Created .env with fresh local secrets. Do not commit or share it.")


if __name__ == "__main__":
    main()
