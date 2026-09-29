"""Declares which MCP servers the orchestrator connects to and how to boot them.

Each entry is spawned as a subprocess over stdio (`sys.executable <script>`),
with its required API key forwarded via environment variables — the same
mechanism Claude Desktop uses in claude_desktop_config.json.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SERVER_SPECS: list[dict] = [
    {
        "alias": "db",
        "script": ROOT / "servers" / "mcp_database.py",
        "api_key_env": "DB_API_KEY",
    },
    {
        "alias": "ticketing",
        "script": ROOT / "servers" / "mcp_ticketing.py",
        "api_key_env": "TICKETING_API_KEY",
    },
    {
        "alias": "kb",
        "script": ROOT / "servers" / "mcp_knowledge.py",
        "api_key_env": "KB_API_KEY",
    },
]
