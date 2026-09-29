"""Anthropic (Claude) LLM backend. See agent/llm.py for the interface contract."""
from __future__ import annotations

from anthropic import AsyncAnthropic

from .llm import LLMTurn, ToolCall, ToolResultItem


class AnthropicClient:
    def __init__(self, model: str = "claude-sonnet-5", max_tokens: int = 1024) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._client = AsyncAnthropic()

    async def create(self, system: str, history: list, tools: list[dict]) -> LLMTurn:
        anthropic_tools = [
            {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools
        ]
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=history,
            tools=anthropic_tools,
        )
        text = "\n".join(block.text for block in response.content if block.type == "text")
        tool_calls = [
            ToolCall(id=block.id, name=block.name, input=block.input)
            for block in response.content
            if block.type == "tool_use"
        ]
        return LLMTurn(
            text=text,
            tool_calls=tool_calls,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            raw_assistant_content=response.content,
        )

    def append_user_text(self, history: list, text: str) -> None:
        history.append({"role": "user", "content": text})

    def append_assistant_turn(self, history: list, turn: LLMTurn) -> None:
        history.append({"role": "assistant", "content": turn.raw_assistant_content})

    def append_tool_results(self, history: list, results: list[ToolResultItem]) -> None:
        blocks = [
            {"type": "tool_result", "tool_use_id": r.id, "content": r.content, "is_error": r.is_error}
            for r in results
        ]
        history.append({"role": "user", "content": blocks})
