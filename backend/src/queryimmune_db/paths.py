"""Locate application resources in source checkouts and built images."""

from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    """Return the directory containing migrations, seed data, and web assets."""
    module_path = Path(__file__).resolve()
    candidates = (module_path.parents[2], Path.cwd(), Path.cwd() / "backend")
    for candidate in candidates:
        if all((candidate / resource).is_dir() for resource in ("migrations", "seed", "web")):
            return candidate
    raise RuntimeError("could not locate migrations/, seed/, and web/ beside the application")
