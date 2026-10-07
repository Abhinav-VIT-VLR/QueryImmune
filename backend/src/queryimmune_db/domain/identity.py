from dataclasses import dataclass


@dataclass(frozen=True)
class Actor:
    user_id: str
    subject: str
    display_name: str
    roles: frozenset[str]
