"""The agent loop: an LLM-driven ReAct orchestrator with a mandatory
human-in-the-loop (HITL) gate in front of every write-action MCP tool.

`run_turn` is an async generator that yields `AgentEvent`s as the turn
progresses. When it yields `ApprovalRequired`, the CALLER MUST resume it with
`await agen.asend(True_or_False)` — the generator will not proceed to
execute (or skip) that tool call until it receives a decision. This makes
HITL approval a structural property of the control flow, not something a
prompt merely asks the model to do.

Whether a tool needs approval is decided entirely by the MCP tool's own
`readOnlyHint` annotation (see agent/mcp_client.py) — the orchestrator never
hardcodes a list of "dangerous" tool names.

This module is LLM-provider-agnostic: it talks only to the `LLMClient`
interface (agent/llm.py), never to a specific SDK, so the same loop runs
identically over Claude or Gemini (agent/llm_factory.py picks the backend).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Optional, Union

from .llm import LLMClient, ToolResultItem
from .mcp_client import MCPToolRouter
from .tracing import Tracer

SYSTEM_PROMPT = """You are the Lieferblick GmbH Internal Operations Agent, used by support engineers.

Rules:
- Always ground factual claims in tool results. Never invent customer, shipment, ticket, or policy data.
- When citing policy information, always include the exact `source` field returned by the knowledge-base tool.
- Reply in the same language the user wrote in (German or English). Default to English if unclear.
- For multi-system questions (e.g. "what happened with order X across all systems"), check the
  database tools AND the ticketing tools before answering — do not answer from only one system.
- Write-action tools (create_ticket, update_ticket, assign_ticket) require human approval. You do
  not need to ask the user yourself — the system pauses automatically before running them. Just
  call the tool when it's the right action, then continue based on whether it was approved or denied.
- If a tool call fails or is denied, explain plainly what happened and suggest a next step. Never
  fabricate a result for a tool call that failed or was denied.
"""


@dataclass
class AssistantText:
    text: str


@dataclass
class ApprovalRequired:
    tool_name: str
    args: dict


@dataclass
class ToolExecuted:
    tool_name: str
    args: dict
    result: Any
    error: bool
    latency_s: float


@dataclass
class ToolDenied:
    tool_name: str
    args: dict


@dataclass
class Done:
    pass


AgentEvent = Union[AssistantText, ApprovalRequired, ToolExecuted, ToolDenied, Done]


class Orchestrator:
    def __init__(
        self,
        router: MCPToolRouter,
        tracer: Tracer,
        llm: LLMClient,
        max_turns: int = 8,
    ) -> None:
        self.router = router
        self.tracer = tracer
        self.llm = llm
        self.max_turns = max_turns

    @property
    def model(self) -> str:
        return self.llm.model

    async def run_turn(self, user_text: str, history: list) -> AsyncGenerator[AgentEvent, Optional[bool]]:
        """Runs one full ReAct turn, mutating `history` in place as it goes.

        Yields AgentEvents. When an ApprovalRequired event is yielded, the
        caller must resume with `agen.asend(True_or_False)` before the
        generator will advance further.
        """
        self.llm.append_user_text(history, user_text)

        for _ in range(self.max_turns):
            t0 = time.time()
            turn = await self.llm.create(SYSTEM_PROMPT, history, self.router.tool_schemas())
            latency = time.time() - t0
            self.tracer.log_llm_call(self.llm.model, latency, turn.input_tokens, turn.output_tokens)

            self.llm.append_assistant_turn(history, turn)

            if turn.text:
                yield AssistantText(turn.text)

            if not turn.tool_calls:
                yield Done()
                return

            results: list[ToolResultItem] = []
            for tc in turn.tool_calls:
                if not self.router.is_read_only(tc.name):
                    approved = yield ApprovalRequired(tc.name, tc.input)
                    if not approved:
                        self.tracer.log_denied(tc.name, tc.input)
                        yield ToolDenied(tc.name, tc.input)
                        results.append(
                            ToolResultItem(
                                id=tc.id,
                                name=tc.name,
                                content=(
                                    "The human operator DENIED this action. Do not retry it this "
                                    "turn; explain to the user that it was not approved."
                                ),
                                is_error=True,
                            )
                        )
                        continue

                t1 = time.time()
                error = False
                try:
                    result = await self.router.call_tool(tc.name, tc.input)
                except Exception as exc:  # noqa: BLE001 - surface every tool failure to the user, never crash
                    result = f"Tool error: {exc}"
                    error = True
                dt = time.time() - t1

                self.tracer.log_tool_call(tc.name, tc.input, result, dt, error)
                yield ToolExecuted(tc.name, tc.input, result, error, dt)

                results.append(ToolResultItem(id=tc.id, name=tc.name, content=str(result), is_error=error))

            self.llm.append_tool_results(history, results)

        yield AssistantText(
            "I've hit the maximum number of reasoning steps for this request. "
            "Please rephrase or narrow your question."
        )
        yield Done()
