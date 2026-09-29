"""Picks an LLM backend from environment variables.

Set LLM_PROVIDER=anthropic|gemini to force a choice. Otherwise: Anthropic is
used if ANTHROPIC_API_KEY is set, else Gemini if GEMINI_API_KEY is set.
"""
from __future__ import annotations

import os

from .llm import LLMClient


def build_llm_client() -> LLMClient:
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()

    if not provider:
        if os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.environ.get("GEMINI_API_KEY"):
            provider = "gemini"
        else:
            raise RuntimeError(
                "No LLM credentials found. Set ANTHROPIC_API_KEY or GEMINI_API_KEY in your "
                ".env file (optionally set LLM_PROVIDER=anthropic|gemini to force one)."
            )

    if provider == "anthropic":
        from .llm_anthropic import AnthropicClient

        return AnthropicClient(model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"))

    if provider == "gemini":
        from .llm_gemini import GeminiClient

        return GeminiClient(model=os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest"))

    raise RuntimeError(f"Unknown LLM_PROVIDER '{provider}'. Must be 'anthropic' or 'gemini'.")
