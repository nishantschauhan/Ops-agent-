"""MCP client plumbing: connects to all configured MCP servers over stdio and
exposes their combined tool surface to the orchestrator as a flat, namespaced
list of Anthropic-compatible tool schemas.

Tool names are namespaced as `<server_alias>__<tool_name>` (e.g.
`ticketing__create_ticket`) so identically-named tools across servers can
never collide, and so the origin server is always unambiguous when routing a
call or logging a trace entry.
"""
from __future__ import annotations

import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@dataclass
class ConnectedServer:
    alias: str
    session: ClientSession
    tools: list  # list[mcp.types.Tool]


class MCPToolRouter:
    """Owns connections to every MCP server the agent is allowed to use."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self.servers: dict[str, ConnectedServer] = {}
        self._tool_owner: dict[str, tuple[str, str]] = {}  # qualified_name -> (alias, real_name)
        self._tool_readonly: dict[str, bool] = {}

    @classmethod
    async def connect_all(cls, specs: list[dict]) -> "MCPToolRouter":
        router = cls()
        for spec in specs:
            await router._connect_one(spec)
        return router

    async def _connect_one(self, spec: dict) -> None:
        api_key_env = spec["api_key_env"]
        if not os.environ.get(api_key_env):
            raise RuntimeError(
                f"Missing {api_key_env} in environment; cannot start '{spec['alias']}' MCP server."
            )

        params = StdioServerParameters(
            command=sys.executable,
            args=[str(spec["script"])],
            env=dict(os.environ),
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        listed = await session.list_tools()
        for tool in listed.tools:
            qualified = f"{spec['alias']}__{tool.name}"
            self._tool_owner[qualified] = (spec["alias"], tool.name)
            self._tool_readonly[qualified] = bool(tool.annotations and tool.annotations.readOnlyHint)

        self.servers[spec["alias"]] = ConnectedServer(alias=spec["alias"], session=session, tools=listed.tools)

    def tool_schemas(self) -> list[dict]:
        """Flat list of {name, description, input_schema} — provider-agnostic.

        Each LLM backend (agent/llm_anthropic.py, agent/llm_gemini.py)
        converts this into its own SDK's tool/function-declaration format.
        """
        out = []
        for alias, conn in self.servers.items():
            for tool in conn.tools:
                out.append(
                    {
                        "name": f"{alias}__{tool.name}",
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema,
                    }
                )
        return out

    def is_read_only(self, qualified_name: str) -> bool:
        """Unknown tools default to False (require approval) — fail safe, not open."""
        return self._tool_readonly.get(qualified_name, False)

    async def call_tool(self, qualified_name: str, args: dict) -> str:
        if qualified_name not in self._tool_owner:
            raise ValueError(f"Unknown tool '{qualified_name}'")
        alias, real_name = self._tool_owner[qualified_name]
        session = self.servers[alias].session

        result = await session.call_tool(real_name, args)
        text = "\n".join(getattr(block, "text", str(block)) for block in result.content)
        if result.isError:
            raise RuntimeError(text or f"'{qualified_name}' returned an error with no message.")
        return text

    async def aclose(self) -> None:
        await self._stack.aclose()
