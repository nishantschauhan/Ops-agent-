"""Simulated boot-time authentication for the MCP servers.

MCP's stdio transport has no per-request headers, so we simulate an API-key
gate the way it would work in a real deployment behind a process supervisor:
each server refuses to boot at all unless its API key is present in the
environment. If an "expected" value is also configured (e.g. rotated by an
ops team without updating the caller), the server additionally verifies the
two match and refuses to boot on a mismatch.
"""
from __future__ import annotations

import os
import sys


def require_api_key(service_name: str) -> str:
    """Validate `<SERVICE_NAME>_API_KEY` (and optional `_EXPECTED` variant).

    Exits the process with a non-zero status if the key is missing or does
    not match the expected value, so the server never accepts a connection
    without credentials.
    """
    key_var = f"{service_name}_API_KEY"
    expected_var = f"{service_name}_API_KEY_EXPECTED"

    provided = os.environ.get(key_var)
    if not provided:
        print(
            f"FATAL: {key_var} is not set in the environment. "
            f"The {service_name} MCP server refuses to start without credentials.",
            file=sys.stderr,
        )
        sys.exit(1)

    expected = os.environ.get(expected_var)
    if expected is not None and provided != expected:
        print(
            f"FATAL: {key_var} does not match {expected_var}. "
            f"The {service_name} MCP server refuses to start with invalid credentials.",
            file=sys.stderr,
        )
        sys.exit(1)

    return provided
