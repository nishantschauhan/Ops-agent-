#!/usr/bin/env python3
"""Lieferblick Database MCP Server (read-only).

Exposes strictly read-only access to the `customers` and `shipments` tables
via three typed tools. No tool accepts raw SQL from the caller — every query
is parameterized and status/limit inputs are validated against an allow-list,
so there is no SQL-injection surface. The SQLite connection is additionally
opened in `mode=ro` so a write would fail even if application logic had a bug.

Run standalone (e.g. from Claude Desktop):
    DB_API_KEY=dev-db-key python servers/mcp_database.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from common.auth import require_api_key
from common.paths import data_dir

require_api_key("DB")

mcp = FastMCP("lieferblick-database")

ALLOWED_STATUSES = {
    "pending",
    "processing",
    "in_transit",
    "delayed",
    "delivered",
    "damaged",
    "returned",
    "cancelled",
}

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


class CustomerOut(BaseModel):
    customer_id: str
    name: str
    company: str
    email: str
    country: str
    preferred_language: str
    tier: str


class ShipmentOut(BaseModel):
    shipment_id: str
    order_ref: str
    customer_id: str
    status: str
    carrier: str
    origin: str
    destination: str
    created_at: str
    updated_at: str
    notes: Optional[str] = None


def _connect() -> sqlite3.Connection:
    db_path = data_dir() / "lieferblick.db"
    if not db_path.exists():
        raise RuntimeError(f"Database not found at {db_path}. Run `python seed_data.py` first.")
    # mode=ro: the OS/driver enforces read-only, independent of application logic.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@mcp.tool(annotations=READ_ONLY)
def get_customer(customer_id: str) -> dict:
    """Look up a single customer by their unique customer ID (e.g. 'CUST-1004').

    Returns the customer's name, company, contact email, country, preferred
    language and support tier. Returns {"error": ...} if no customer matches.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
    if row is None:
        return {"error": f"No customer found with id '{customer_id}'."}
    return CustomerOut(**dict(row)).model_dump()


@mcp.tool(annotations=READ_ONLY)
def list_shipments(
    customer_id: Optional[str] = None,
    order_ref: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """List shipments, optionally filtered by exact customer_id and/or order_ref.

    Returns up to `limit` (max 100) shipments, most recently updated first.
    Use this to find a customer's shipment history, or to look up a specific
    order_ref (e.g. 'ORD-8890') across the shipment table.
    """
    limit = max(1, min(limit, 100))
    query = "SELECT * FROM shipments WHERE 1=1"
    params: list = []
    if customer_id:
        query += " AND customer_id = ?"
        params.append(customer_id)
    if order_ref:
        query += " AND order_ref = ?"
        params.append(order_ref)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return {"shipments": [ShipmentOut(**dict(r)).model_dump() for r in rows]}


@mcp.tool(annotations=READ_ONLY)
def search_shipments_by_status(status: str, limit: int = 20) -> dict:
    """Search shipments by delivery status.

    Valid statuses: pending, processing, in_transit, delayed, delivered,
    damaged, returned, cancelled. Returns {"error": ...} for any other value.
    """
    normalized = status.strip().lower()
    if normalized not in ALLOWED_STATUSES:
        return {"error": f"Invalid status '{status}'. Must be one of: {sorted(ALLOWED_STATUSES)}"}
    limit = max(1, min(limit, 100))

    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM shipments WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
            (normalized, limit),
        ).fetchall()
    return {"shipments": [ShipmentOut(**dict(r)).model_dump() for r in rows]}


if __name__ == "__main__":
    mcp.run(transport="stdio")
