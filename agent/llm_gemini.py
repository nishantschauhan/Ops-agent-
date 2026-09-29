"""Google Gemini LLM backend. See agent/llm.py for the interface contract.

Verified against google-genai==2.25.0: `FunctionDeclaration.parameters_json_schema`
accepts a plain JSON Schema dict directly (the same shape MCP tools already
expose), so no schema-format translation is needed beyond stripping the two
keys ($schema, title) Gemini's validator rejects.
"""
from __future__ import annotations

import os

from google import genai
from google.genai import types

from .llm import LLMTurn, ToolCall, ToolResultItem


class GeminiClient:
    def __init__(self, model: str = "gemini-flash-lite-latest", max_tokens: int = 2048) -> None:
        self.model = model
        self.max_tokens = max_tokens
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set. Add it to your .env file.")
        self._client = genai.Client(api_key=api_key)

    def _to_gemini_tools(self, tools: list[dict]) -> list[types.Tool]:
        declarations = []
        for t in tools:
            schema = dict(t["input_schema"])
            schema.pop("$schema", None)
            schema.pop("title", None)
            declarations.append(
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters_json_schema=schema,
                )
            )
        return [types.Tool(function_declarations=declarations)]

    async def create(self, system: str, history: list, tools: list[dict]) -> LLMTurn:
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=self.max_tokens,
            tools=self._to_gemini_tools(tools),
        )
        response = await self._client.aio.models.generate_content(
            model=self.model,
            contents=history,
            config=config,
        )
        candidate = response.candidates[0]
        text = response.text or ""
        tool_calls = [
            ToolCall(id=fc.id or fc.name, name=fc.name, input=dict(fc.args or {}))
            for fc in (response.function_calls or [])
        ]
        usage = response.usage_metadata
        return LLMTurn(
            text=text,
            tool_calls=tool_calls,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            raw_assistant_content=candidate.content,
        )

    def append_user_text(self, history: list, text: str) -> None:
        history.append(types.Content(role="user", parts=[types.Part(text=text)]))

    def append_assistant_turn(self, history: list, turn: LLMTurn) -> None:
        history.append(turn.raw_assistant_content)

    def append_tool_results(self, history: list, results: list[ToolResultItem]) -> None:
        parts = [
            types.Part.from_function_response(
                name=r.name,
                response={"error": r.content} if r.is_error else {"result": r.content},
            )
            for r in results
        ]
        history.append(types.Content(role="user", parts=parts))
