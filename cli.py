#!/usr/bin/env python3
"""Command-line interface for the Lieferblick Internal Operations Agent.

    python cli.py

Connects to all three MCP servers, then runs an interactive chat loop.
Write-action tool calls pause for an explicit [y/N] approval, showing the
exact tool name and arguments before anything executes.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

from agent.config import SERVER_SPECS
from agent.llm_factory import build_llm_client
from agent.mcp_client import MCPToolRouter
from agent.orchestrator import ApprovalRequired, AssistantText, Done, Orchestrator, ToolDenied, ToolExecuted
from agent.tracing import Tracer

load_dotenv()


def _fmt_latency(seconds: float) -> str:
    return f"{seconds * 1000:.0f}ms" if seconds < 1 else f"{seconds:.2f}s"


async def main() -> None:
    llm = build_llm_client()  # raises with a clear message if no provider is configured

    print("Connecting to MCP servers (database, ticketing, knowledge-base)...")
    router = await MCPToolRouter.connect_all(SERVER_SPECS)
    print(f"Connected via {llm.model}. {len(router.tool_schemas())} tools available. Type 'exit' to quit.\n")

    tracer = Tracer(log_path=Path("data/trace.jsonl"))
    orchestrator = Orchestrator(router, tracer, llm)
    history: list[dict] = []

    try:
        while True:
            try:
                user_text = input("You> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user_text:
                continue
            if user_text.lower() in {"exit", "quit"}:
                break

            agen = orchestrator.run_turn(user_text, history)
            send_value: bool | None = None
            try:
                while True:
                    event = await agen.asend(send_value)
                    send_value = None

                    if isinstance(event, AssistantText):
                        print(f"\nAgent> {event.text}\n")
                    elif isinstance(event, ToolExecuted):
                        status = "ERROR" if event.error else "ok"
                        print(f"  [tool:{status}] {event.tool_name}({event.args}) -> {_fmt_latency(event.latency_s)}")
                    elif isinstance(event, ToolDenied):
                        print(f"  [tool:denied] {event.tool_name}({event.args})")
                    elif isinstance(event, ApprovalRequired):
                        print(f"\n⚠️  APPROVAL REQUIRED: {event.tool_name}")
                        print(f"   arguments: {event.args}")
                        ans = input("   Approve this action? [y/N]: ").strip().lower()
                        send_value = ans in {"y", "yes"}
                    elif isinstance(event, Done):
                        break
            except StopAsyncIteration:
                pass
    finally:
        await router.aclose()
        print(
            f"\nSession trace written to data/trace.jsonl "
            f"({tracer.total_input_tokens} in / {tracer.total_output_tokens} out tokens, "
            f"~${tracer.estimate_cost_usd(orchestrator.model):.4f} est.)"
        )


if __name__ == "__main__":
    asyncio.run(main())
