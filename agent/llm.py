"""Provider-agnostic LLM backend interface.

The orchestrator's ReAct loop (agent/orchestrator.py) talks only to this
interface, never to a specific SDK. This is what lets the same HITL loop and
tracing run identically over Claude (agent/llm_anthropic.py) or Gemini
(agent/llm_gemini.py) — swap the backend via agent/llm_factory.py without
touching the orchestrator.

Conversation history is kept in each provider's own native format (a plain
Anthropic messages list, or a list of google.genai `Content` objects) — the
orchestrator never inspects it directly, it only ever calls the four methods
below, so the native format can't leak into provider-agnostic code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMTurn:
    text: str
    tool_calls: list[ToolCall]
    input_tokens: int
    output_tokens: int
    raw_assistant_content: Any  # opaque; only the backend that produced it reads it back


@dataclass
class ToolResultItem:
    id: str
    name: str
    content: str
    is_error: bool


class LLMClient(Protocol):
    model: str

    async def create(self, system: str, history: list, tools: list[dict]) -> LLMTurn:
        """One model call. `tools` is MCPToolRouter.tool_schemas()."""
        ...

    def append_user_text(self, history: list, text: str) -> None:
        ...

    def append_assistant_turn(self, history: list, turn: LLMTurn) -> None:
        ...

    def append_tool_results(self, history: list, results: list[ToolResultItem]) -> None:
        ...
