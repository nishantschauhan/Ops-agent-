#!/usr/bin/env python3
"""Lieferblick Ticketing MCP Server (read/write — the "dangerous" server).

Manages support tickets in a JSON file (data/tickets.json), guarded by an
in-process lock and atomic (write-temp-then-rename) writes so concurrent
tool calls can't corrupt the store.

`create_ticket`, `update_ticket` and `assign_ticket` are annotated
readOnlyHint=False so the agent orchestrator knows to pause for human
approval before calling them (see agent/orchestrator.py). `get_ticket` and
`list_tickets` are read-only lookups and do not require approval.

Run standalone (e.g. from Claude Desktop):
    TICKETING_API_KEY=dev-ticketing-key python servers/mcp_ticketing.py
"""
from __future__ import annotations

import json
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from common.auth import require_api_key
from common.paths import data_dir

require_api_key("TICKETING")

mcp = FastMCP("lieferblick-ticketing")

_LOCK = threading.Lock()

ALLOWED_PRIORITIES = {"low", "normal", "high", "urgent"}
ALLOWED_STATUSES = {"open", "in_progress", "waiting_on_customer", "resolved", "closed"}

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE_ACTION = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


class TicketOut(BaseModel):
    ticket_id: str
    customer_id: str
    order_ref: Optional[str] = None
    subject: str
    description: str
    status: str
    priority: str
    assigned_to: Optional[str] = None
    created_at: str
    updated_at: str
    history: list[dict]


def _tickets_path() -> Path:
    return data_dir() / "tickets.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> list[dict]:
    p = _tickets_path()
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _save(tickets: list[dict]) -> None:
    p = _tickets_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(tickets, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)  # atomic on POSIX


@mcp.tool(annotations=READ_ONLY)
def get_ticket(ticket_id: str) -> dict:
    """Look up a single support ticket by its ticket_id (e.g. 'TCK-8890A1').

    Returns the full ticket including its history log. Returns {"error": ...}
    if no ticket matches.
    """
    for t in _load():
        if t["ticket_id"] == ticket_id:
            return TicketOut(**t).model_dump()
    return {"error": f"No ticket found with id '{ticket_id}'."}


@mcp.tool(annotations=READ_ONLY)
def list_tickets(
    customer_id: Optional[str] = None,
    order_ref: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """List tickets, optionally filtered by customer_id, order_ref and/or status.

    Useful for finding all tickets tied to a given order_ref (e.g. 'ORD-8890')
    when cross-referencing with the shipment/database records.
    """
    limit = max(1, min(limit, 100))
    tickets = _load()
    if customer_id:
        tickets = [t for t in tickets if t["customer_id"] == customer_id]
    if order_ref:
        tickets = [t for t in tickets if t.get("order_ref") == order_ref]
    if status:
        normalized = status.strip().lower()
        if normalized not in ALLOWED_STATUSES:
            return {"error": f"Invalid status '{status}'. Must be one of: {sorted(ALLOWED_STATUSES)}"}
        tickets = [t for t in tickets if t["status"] == normalized]
    tickets = sorted(tickets, key=lambda t: t["updated_at"], reverse=True)[:limit]
    return {"tickets": [TicketOut(**t).model_dump() for t in tickets]}


@mcp.tool(annotations=WRITE_ACTION)
def create_ticket(
    customer_id: str,
    subject: str,
    description: str,
    order_ref: Optional[str] = None,
    priority: str = "normal",
) -> dict:
    """Create a new support ticket for a customer.

    WRITE ACTION — this persists a new record and must be approved by a
    human operator before it executes. priority must be one of: low, normal,
    high, urgent.
    """
    normalized_priority = priority.strip().lower()
    if normalized_priority not in ALLOWED_PRIORITIES:
        return {"error": f"priority must be one of {sorted(ALLOWED_PRIORITIES)}"}

    with _LOCK:
        tickets = _load()
        ticket = {
            "ticket_id": f"TCK-{uuid.uuid4().hex[:8].upper()}",
            "customer_id": customer_id,
            "order_ref": order_ref,
            "subject": subject,
            "description": description,
            "status": "open",
            "priority": normalized_priority,
            "assigned_to": None,
            "created_at": _now(),
            "updated_at": _now(),
            "history": [{"at": _now(), "event": "created"}],
        }
        tickets.append(ticket)
        _save(tickets)
    return TicketOut(**ticket).model_dump()


@mcp.tool(annotations=WRITE_ACTION)
def update_ticket(
    ticket_id: str,
    status: Optional[str] = None,
    note: Optional[str] = None,
    priority: Optional[str] = None,
) -> dict:
    """Update a ticket's status and/or priority, and/or append a note.

    WRITE ACTION — must be approved by a human operator before it executes.
    status must be one of: open, in_progress, waiting_on_customer, resolved,
    closed. priority must be one of: low, normal, high, urgent.
    """
    with _LOCK:
        tickets = _load()
        for t in tickets:
            if t["ticket_id"] != ticket_id:
                continue
            if status is not None:
                normalized = status.strip().lower()
                if normalized not in ALLOWED_STATUSES:
                    return {"error": f"status must be one of {sorted(ALLOWED_STATUSES)}"}
                t["status"] = normalized
                t["history"].append({"at": _now(), "event": f"status -> {normalized}"})
            if priority is not None:
                normalized_p = priority.strip().lower()
                if normalized_p not in ALLOWED_PRIORITIES:
                    return {"error": f"priority must be one of {sorted(ALLOWED_PRIORITIES)}"}
                t["priority"] = normalized_p
                t["history"].append({"at": _now(), "event": f"priority -> {normalized_p}"})
            if note:
                t["history"].append({"at": _now(), "event": f"note: {note}"})
            t["updated_at"] = _now()
            _save(tickets)
            return TicketOut(**t).model_dump()
    return {"error": f"No ticket found with id '{ticket_id}'."}


@mcp.tool(annotations=WRITE_ACTION)
def assign_ticket(ticket_id: str, assignee: str) -> dict:
    """Assign a ticket to a named support engineer or team alias.

    WRITE ACTION — must be approved by a human operator before it executes.
    """
    with _LOCK:
        tickets = _load()
        for t in tickets:
            if t["ticket_id"] != ticket_id:
                continue
            t["assigned_to"] = assignee
            t["updated_at"] = _now()
            t["history"].append({"at": _now(), "event": f"assigned -> {assignee}"})
            _save(tickets)
            return TicketOut(**t).model_dump()
    return {"error": f"No ticket found with id '{ticket_id}'."}


if __name__ == "__main__":
    mcp.run(transport="stdio")
