# Lieferblick Internal Operations Agent

An internal support agent for Lieferblick GmbH support engineers. Plain-language
questions (English or Deutsch) get grounded answers pulled live from three
independent MCP servers — never from hardcoded Python business logic — with a
mandatory human approval gate in front of every state-changing action.

## Architecture

The agent never talks to a database, a ticket store, or a policy corpus
directly. Every fact and every action passes through the Model Context
Protocol. The orchestrator's only "hardcoded" knowledge is *how to speak MCP*
— it discovers each server's tools, schemas, and read/write nature (via MCP
tool annotations) at connection time.

```mermaid
graph TD
    subgraph Client
        UI["Streamlit UI (app.py)"]
        CLI["CLI (cli.py)"]
        ORCH["Orchestrator: Claude ReAct loop + HITL gate\n(agent/orchestrator.py)"]
        UI --> ORCH
        CLI --> ORCH
    end

    ORCH -->|messages.create, tool_use| CLAUDE[("Anthropic Claude API")]

    subgraph MCP -- stdio transport, one subprocess per server
        DB["Database MCP Server\nservers/mcp_database.py\nread-only"]
        TCK["Ticketing MCP Server\nservers/mcp_ticketing.py\nread/write"]
        KB["Knowledge-Base MCP Server\nservers/mcp_knowledge.py\nread-only"]
    end

    ORCH -->|list_tools / call_tool| DB
    ORCH -->|list_tools / call_tool| TCK
    ORCH -->|list_tools / call_tool| KB

    DB --> SQLITE[("SQLite: customers, shipments\nmode=ro connection")]
    TCK --> JSONSTORE[("tickets.json\nlocked, atomic writes")]
    KB --> POLICIES[("policies.json\nTF-IDF index in memory")]
```

**Human-in-the-loop.** `run_turn()` in `agent/orchestrator.py` is an async
generator. Whenever Claude requests a tool whose MCP `readOnlyHint`
annotation is not `true`, the generator `yield`s an `ApprovalRequired` event
and *does not proceed* until the caller resumes it with
`await agen.asend(True_or_False)`. Both `cli.py` (blocking `input()`) and
`app.py` (Streamlit Approve/Deny buttons) drive the same generator — approval
is a property of the control flow, not a prompt instruction the model could
ignore.

**Tracing.** `agent/tracing.py`'s `Tracer` logs every LLM call (latency,
input/output tokens) and every tool call (name, arguments, raw result,
latency, error flag) to `data/trace.jsonl`, and the same events drive the
live sidebar in the Streamlit UI and inline `[tool:...]` lines in the CLI.

**Resilience.** Every tool invocation in the orchestrator is wrapped in
`try/except`; a failing or error-flagged MCP call becomes a
`tool_result` with `is_error=true` that's handed back to the model, which is
instructed to explain the failure rather than fabricate a result. The agent
process itself never crashes on a downstream tool error.

**LLM-agnostic.** `agent/orchestrator.py` never talks to Claude or Gemini
directly — it calls the small `LLMClient` interface in `agent/llm.py`.
`agent/llm_anthropic.py` and `agent/llm_gemini.py` are the two
implementations; `agent/llm_factory.py` picks one from environment variables.
Same ReAct loop, same HITL gate, same tracing, either provider — set
`ANTHROPIC_API_KEY` or `GEMINI_API_KEY` in `.env` and it runs (force a choice
with `LLM_PROVIDER=anthropic|gemini` if both are set).

## Repository layout

```
lieferblick-ops-agent/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── seed_data.py                 # generates data/lieferblick.db, tickets.json, policies.json
├── common/
│   ├── auth.py                  # simulated boot-time API-key gate
│   └── paths.py                 # shared data/ directory resolution
├── servers/
│   ├── mcp_database.py          # read-only: get_customer, list_shipments, search_shipments_by_status
│   ├── mcp_ticketing.py         # read/write: create_ticket, update_ticket, assign_ticket (+ get/list)
│   └── mcp_knowledge.py         # read-only: search_docs (TF-IDF), list_policies
├── agent/
│   ├── config.py                # which servers to spawn and how
│   ├── mcp_client.py            # MCPToolRouter: connects to all servers, namespaces their tools
│   ├── llm.py                   # LLMClient interface (provider-agnostic)
│   ├── llm_anthropic.py         # Claude backend
│   ├── llm_gemini.py            # Gemini backend
│   ├── llm_factory.py           # picks a backend from env vars
│   ├── orchestrator.py          # the ReAct loop + HITL gate
│   └── tracing.py               # Tracer
├── cli.py                       # terminal chat interface
└── app.py                       # Streamlit chat interface + live trace sidebar
```

## Running locally (no Docker)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY or GEMINI_API_KEY

python seed_data.py              # generates data/lieferblick.db, tickets.json, policies.json

python cli.py                    # terminal chat
# or
streamlit run app.py             # web UI at http://localhost:8501
```

## Running via Docker

```bash
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY or GEMINI_API_KEY

docker compose up --build
# UI at http://localhost:8501
```

`app` is the whole environment: on start it seeds `./data` (idempotent — it
skips files that already exist, so tickets created during a demo survive a
restart) and launches Streamlit, which in turn spawns the three MCP servers
as internal subprocesses over stdio.

Three additional `profiles: ["debug"]` services (`db-server`,
`ticketing-server`, `kb-server`) are defined for exercising a single server
in isolation, e.g.:

```bash
docker compose --profile debug run --rm db-server
```

## Connecting Claude Desktop directly

Because MCP stdio servers are spawned as local child processes, point Claude
Desktop at your local Python interpreter (the one with `requirements.txt`
installed) and the server script's absolute path. Add this to your
`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "lieferblick-database": {
      "command": "/absolute/path/to/lieferblick-ops-agent/.venv/bin/python",
      "args": ["/absolute/path/to/lieferblick-ops-agent/servers/mcp_database.py"],
      "env": {
        "DB_API_KEY": "dev-db-key",
        "LIEFERBLICK_DATA_DIR": "/absolute/path/to/lieferblick-ops-agent/data"
      }
    },
    "lieferblick-ticketing": {
      "command": "/absolute/path/to/lieferblick-ops-agent/.venv/bin/python",
      "args": ["/absolute/path/to/lieferblick-ops-agent/servers/mcp_ticketing.py"],
      "env": {
        "TICKETING_API_KEY": "dev-ticketing-key",
        "LIEFERBLICK_DATA_DIR": "/absolute/path/to/lieferblick-ops-agent/data"
      }
    },
    "lieferblick-knowledge": {
      "command": "/absolute/path/to/lieferblick-ops-agent/.venv/bin/python",
      "args": ["/absolute/path/to/lieferblick-ops-agent/servers/mcp_knowledge.py"],
      "env": {
        "KB_API_KEY": "dev-kb-key",
        "LIEFERBLICK_DATA_DIR": "/absolute/path/to/lieferblick-ops-agent/data"
      }
    }
  }
}
```

On Windows use `.venv\Scripts\python.exe`. Run `python seed_data.py` at least
once before Claude Desktop connects, so the servers have data to read. Claude
Desktop's own approval prompts apply to `create_ticket` / `update_ticket` /
`assign_ticket` the same way they do to any other tool call it makes — the
`readOnlyHint=false` annotation on those three tools is what triggers that
built-in confirmation UI.

## Demo query

The seed data guarantees an order (`ORD-8890`, customer `CUST-1004` / Jonas
Becker) with both a `damaged` shipment record and an `in_progress` ticket
(`TCK-8890A1`), so this prompt exercises the full read path across two
servers plus a knowledge-base citation:

> "Summarize what happened with order ORD-8890 across all systems, and tell
> me what our refund policy says about it."

To see the HITL gate fire, follow up with something like:

> "Escalate that ticket to priority urgent and assign it to
> support-team-de@lieferblick.internal."

## Security notes

- **No raw SQL tool.** The database server exposes only three purpose-built,
  parameterized tools — there is no tool through which the LLM can submit
  free-text SQL, and the SQLite connection is opened `mode=ro` as a second
  line of defense.
- **Simulated auth.** Each server calls `common.auth.require_api_key()` at
  import time and exits immediately if its `<NAME>_API_KEY` env var is
  missing (or doesn't match an optional `<NAME>_API_KEY_EXPECTED`), so it
  never accepts a connection without credentials.
- **Fail-safe HITL default.** `MCPToolRouter.is_read_only()` defaults to
  `False` for any tool it doesn't recognize as explicitly read-only — an
  unannotated or newly added tool requires approval rather than running
  freely.
