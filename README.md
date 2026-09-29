# Internal Operations Agent

An LLM agent that answers operational questions and takes actions by talking to real systems through **Model Context Protocol (MCP) servers** — not hardcoded tools. Built for a logistics-ops use case: query shipments, search policy docs, and create tickets, with a human approval gate before anything is written.

> **Why this exists:** most "AI agent" demos call one LLM and hardcode their tools. This project is built the way agentic systems are actually shipped in production: tools live in standalone, reusable MCP servers; every write action requires human approval; and every request produces a full trace of which tools ran, with what arguments, and what they cost.

![demo](docs/demo.gif)

---

## What it does

A support engineer asks in plain language, and the agent plans, calls the right tools, and returns a grounded answer or a proposed action:

| Question | What the agent does |
|---|---|
| "How many open shipments does Meyer AG have, and which are delayed?" | Queries the **database** MCP server |
| "What's our policy on damaged-goods refunds?" | Searches the **knowledge-base** MCP server, answers with citations |
| "Create a ticket for delayed shipment #4471, assign it to returns." | Proposes a **ticketing** write action → **waits for human approval** → executes |
| "Summarize what happened with order 8890 across all systems." | Chains multiple tools and combines the results |

---

## Architecture

```
                    ┌──────────────────┐
   user question →  │   Agent loop     │  plan → call tools → synthesize
                    │  (LLM + tracing) │
                    └────────┬─────────┘
                             │ MCP protocol
            ┌────────────────┼────────────────┐
            ▼                ▼                ▼
    ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
    │ Database MCP │ │ Ticketing MCP│ │ Knowledge MCP│
    │  (read-only) │ │   (writes)   │ │ (vector RAG) │
    └──────────────┘ └──────────────┘ └──────────────┘
      API-key auth     API-key auth     API-key auth
      + write approval gate
```

Each MCP server is a standalone process. Because they speak MCP, they can be plugged into any MCP client — including Claude Desktop — not just this agent. See [Using the servers in Claude Desktop](#using-the-servers-in-claude-desktop).

---

## Key engineering decisions

These are the parts I'd want a reviewer to look at:

- **Human-in-the-loop for writes.** Read tools run freely; any tool that mutates state (`create_ticket`, `update_ticket`, `assign_ticket`) returns a *proposed action* that the user must confirm before it executes. See `agent/approval.py`.
- **Read-only DB access, guarded.** The database server exposes typed query tools only — no raw SQL passes through. Inputs are validated with Pydantic and parameters are bound, so tool calls can't be turned into destructive queries.
- **Graceful failure.** Tool timeouts, empty results, and server errors are caught and surfaced to the user as honest messages; the agent never fabricates a result when a tool fails. Retries with backoff on transient errors.
- **Grounded answers with citations.** Knowledge-base answers include the source document for every claim. No source, no claim.
- **Auth on every server.** Each MCP server checks an API key before serving tools.
- **Full request tracing.** Every request logs the tool calls, arguments, results, latency, and token cost. Example trace in [Observability](#observability).

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Agent orchestration | *(your choice — e.g. LangGraph / Pydantic AI)* | Explicit state and control over the tool-calling loop |
| Tool layer | **MCP** (official SDK) | Standard, reusable, client-agnostic |
| Data validation | Pydantic | Typed, validated tool I/O |
| Database | *(e.g. Postgres / SQLite)* | Seeded with synthetic shipment/customer data |
| Vector search | *(e.g. Qdrant / pgvector)* | Semantic search over policy docs |
| Tracing | *(e.g. Langfuse)* | Per-request traces of tools, cost, latency |
| Packaging | Docker Compose | One command brings the whole system up |

> Fill in the bracketed choices with what you actually used, and delete this note.

---

## Quick start

```bash
git clone <repo-url>
cd internal-operations-agent
cp .env.example .env        # add your LLM API key
docker compose up           # starts the 3 MCP servers + agent
python scripts/seed.py      # loads synthetic data
```

Then ask a question:

```bash
python -m agent "How many open shipments does Meyer AG have, and which are delayed?"
```

---

## Using the servers in Claude Desktop

The MCP servers are standalone. To confirm, add the ticketing server to Claude Desktop's config and the tools appear directly in the client:

```json
{
  "mcpServers": {
    "ticketing": {
      "command": "python",
      "args": ["-m", "servers.ticketing"],
      "env": { "TICKETING_API_KEY": "..." }
    }
  }
}
```

![claude desktop tools](docs/claude-desktop.png)

---

## Observability

Every request produces a trace:

```
REQUEST  "Create a ticket for delayed shipment #4471, assign to returns"
├─ tool  list_shipments(status="delayed", id="4471")      42ms   ok
├─ gate  create_ticket(...)  → AWAITING HUMAN APPROVAL
├─ ✅ approved by user
├─ tool  create_ticket(shipment="4471", team="returns")   88ms   ok  → TCK-2033
└─ done  2 tools · 1.9s · 3,410 tokens · €0.004
```

---

## Evaluation

*(If you built the bonus eval set, describe it here.)* A suite of ~18 test questions checks that the agent calls the right tools, cites sources, and always triggers the approval gate on writes. Run with:

```bash
python -m eval.run
```

| Metric | Result |
|---|---|
| Correct tool selection | _e.g. 17/18_ |
| Citations present on KB answers | _e.g. 100%_ |
| Approval gate triggered on all writes | _e.g. 100%_ |

---

## Project structure

```
agent/            # agent loop, approval gate, tracing
servers/
  database/       # read-only shipments/customers MCP server
  ticketing/      # write-capable ticketing MCP server
  knowledge/      # vector-search MCP server over policy docs
scripts/seed.py   # loads synthetic data
eval/             # test questions + runner
docs/             # diagram, demo gif, screenshots
docker-compose.yml
```

---

## What I'd do next

Honest scope notes — where I stubbed things and what production would add:

- Synthetic data instead of a real warehouse system (architecture was the focus).
- Auth is a shared API key; production would use per-client tokens / OAuth.
- *(add your own — reviewers trust honest limitations more than a fake-complete demo)*
