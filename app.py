"""Streamlit UI for the Lieferblick Internal Operations Agent.

    streamlit run app.py

Chat interface on the left; a live Tool Call Trace in the sidebar. Any
write-action tool call (create_ticket, update_ticket, assign_ticket) pauses
the conversation and renders an explicit Approve/Deny prompt before it runs.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from agent.config import SERVER_SPECS
from agent.llm_factory import build_llm_client
from agent.mcp_client import MCPToolRouter
from agent.orchestrator import ApprovalRequired, AssistantText, Done, Orchestrator, ToolDenied, ToolExecuted
from agent.tracing import Tracer

load_dotenv()

st.set_page_config(page_title="Lieferblick Ops Agent", page_icon="\U0001F4E6", layout="wide")


def get_loop() -> asyncio.AbstractEventLoop:
    if "loop" not in st.session_state:
        st.session_state.loop = asyncio.new_event_loop()
    return st.session_state.loop


def get_router() -> MCPToolRouter:
    if "router" not in st.session_state:
        loop = get_loop()
        with st.spinner("Starting MCP servers (database, ticketing, knowledge-base)..."):
            st.session_state.router = loop.run_until_complete(MCPToolRouter.connect_all(SERVER_SPECS))
    return st.session_state.router


def get_orchestrator() -> Orchestrator:
    if "orchestrator" not in st.session_state:
        st.session_state.tracer = Tracer(log_path=Path("data/trace.jsonl"))
        st.session_state.orchestrator = Orchestrator(get_router(), st.session_state.tracer, st.session_state.llm)
    return st.session_state.orchestrator


def get_llm():
    if "llm" not in st.session_state:
        st.session_state.llm = build_llm_client()
    return st.session_state.llm


def init_state() -> None:
    st.session_state.setdefault("chat", [])  # list[(role, text)]
    st.session_state.setdefault("trace", [])  # list[ToolExecuted | ToolDenied]
    st.session_state.setdefault("history", [])  # raw Anthropic message history
    st.session_state.setdefault("pending_agen", None)
    st.session_state.setdefault("pending_event", None)


def pump(agen, send_value):
    """Advance the orchestrator's async generator until it needs approval or finishes."""
    loop = get_loop()
    while True:
        try:
            event = loop.run_until_complete(agen.asend(send_value))
        except StopAsyncIteration:
            return None
        send_value = None

        if isinstance(event, AssistantText):
            st.session_state.chat.append(("assistant", event.text))
        elif isinstance(event, (ToolExecuted, ToolDenied)):
            st.session_state.trace.append(event)
        elif isinstance(event, ApprovalRequired):
            return event
        elif isinstance(event, Done):
            return None


def main() -> None:
    init_state()

    try:
        get_llm()
    except Exception as exc:
        st.error(str(exc))
        st.info("See .env.example — set ANTHROPIC_API_KEY or GEMINI_API_KEY, then restart.")
        st.stop()

    st.title("\U0001F4E6 Lieferblick Internal Operations Agent")
    st.caption("Ask about customers, shipments, tickets, or policy — in English or Deutsch.")

    orchestrator = get_orchestrator()

    for role, text in st.session_state.chat:
        st.chat_message(role).write(text)

    pending = st.session_state.pending_event
    if pending is not None:
        with st.chat_message("assistant"):
            st.warning("⚠️ This action needs your approval before it runs.")
            st.markdown(f"**Tool:** `{pending.tool_name}`")
            st.json(pending.args)
            c1, c2 = st.columns(2)
            approve = c1.button("✅ Approve", use_container_width=True)
            deny = c2.button("❌ Deny", use_container_width=True)
        if approve or deny:
            nxt = pump(st.session_state.pending_agen, approve)
            st.session_state.pending_event = nxt
            if nxt is None:
                st.session_state.pending_agen = None
            st.rerun()
    else:
        user_text = st.chat_input("z.B. 'Was ist mit Bestellung ORD-8890 passiert?'")
        if user_text:
            st.session_state.chat.append(("user", user_text))
            agen = orchestrator.run_turn(user_text, st.session_state.history)
            nxt = pump(agen, None)
            st.session_state.pending_agen = agen
            st.session_state.pending_event = nxt
            if nxt is None:
                st.session_state.pending_agen = None
            st.rerun()

    with st.sidebar:
        st.header("\U0001F50D Tool Call Trace")
        if not st.session_state.trace:
            st.caption("No tool calls yet this session.")
        for t in reversed(st.session_state.trace):
            if isinstance(t, ToolExecuted):
                icon = "❌" if t.error else "✅"
                with st.expander(f"{icon} {t.tool_name} — {t.latency_s * 1000:.0f}ms"):
                    st.markdown("**Arguments**")
                    st.json(t.args)
                    st.markdown("**Result**")
                    st.code(str(t.result)[:3000])
            else:
                with st.expander(f"\U0001F6AB DENIED — {t.tool_name}"):
                    st.json(t.args)

        st.divider()
        tracer = st.session_state.tracer
        st.metric("Input tokens", tracer.total_input_tokens)
        st.metric("Output tokens", tracer.total_output_tokens)
        st.metric("Est. cost (USD)", f"${tracer.estimate_cost_usd(orchestrator.model):.4f}")


if __name__ == "__main__":
    main()
