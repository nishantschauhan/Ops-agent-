"""Structured, readable trace logging for every agent turn.

Every LLM call and every tool call (including denials) is recorded with
arguments, raw results, latency, and token usage, both in-memory (for the
UI/CLI to render live) and appended to a JSONL file for later inspection.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Approximate USD price per million tokens. For rough cost visibility only —
# not a source of truth for billing. Check current provider pricing pages
# before relying on this for anything beyond a ballpark.
PRICE_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
    "gemini-flash-lite-latest": {"input": 0.10, "output": 0.40},
}


@dataclass
class TraceEntry:
    kind: str
    at: str
    detail: dict[str, Any]


class Tracer:
    def __init__(self, log_path: Path | None = None) -> None:
        self.entries: list[TraceEntry] = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.log_path = log_path
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _emit(self, kind: str, detail: dict[str, Any]) -> None:
        entry = TraceEntry(kind=kind, at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"), detail=detail)
        self.entries.append(entry)
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    def log_llm_call(self, model: str, latency_s: float, input_tokens: int, output_tokens: int) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self._emit(
            "llm_call",
            {"model": model, "latency_s": round(latency_s, 3), "input_tokens": input_tokens, "output_tokens": output_tokens},
        )

    def log_tool_call(self, name: str, args: dict, result: Any, latency_s: float, error: bool) -> None:
        result_str = str(result)
        self._emit(
            "tool_call",
            {
                "name": name,
                "args": args,
                "result": result_str[:2000],
                "latency_s": round(latency_s, 3),
                "error": error,
            },
        )

    def log_denied(self, name: str, args: dict) -> None:
        self._emit("tool_denied", {"name": name, "args": args})

    def estimate_cost_usd(self, model: str) -> float:
        prices = PRICE_PER_MTOK.get(model)
        if not prices:
            return 0.0
        return (self.total_input_tokens / 1_000_000) * prices["input"] + (self.total_output_tokens / 1_000_000) * prices["output"]
