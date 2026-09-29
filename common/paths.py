"""Shared filesystem path resolution for the seed script and all three MCP servers."""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Directory holding the SQLite DB, tickets.json and policies.json.

    Overridable via LIEFERBLICK_DATA_DIR so the same code works whether it's
    run from the repo root, inside Docker (/app/data), or from Claude Desktop
    (which may launch the server with an arbitrary working directory).
    """
    default = Path(__file__).resolve().parent.parent / "data"
    d = Path(os.environ.get("LIEFERBLICK_DATA_DIR", default))
    d.mkdir(parents=True, exist_ok=True)
    return d
