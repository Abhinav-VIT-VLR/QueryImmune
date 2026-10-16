"""Generate a password hash for the configured QueryImmune demo login."""
from __future__ import annotations

from getpass import getpass

from queryimmune_db.api.auth import hash_login_password


def main() -> None:
    password = getpass("Login password (12+ characters): ")
    confirmation = getpass("Confirm login password: ")
    if password != confirmation:
        raise SystemExit("Passwords did not match")
    if len(password) < 12 or len(password.encode("utf-8")) > 1024:
        raise SystemExit("Password must be at least 12 characters and no more than 1024 UTF-8 bytes")
    print("AUTH_LOGIN_PASSWORD_HASH=" + hash_login_password(password))


if __name__ == "__main__":
    main()
