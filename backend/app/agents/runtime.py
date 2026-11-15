"""Minimal tool-calling agent runtime shared by the Research chat and the Alerts assistant.

Why not a framework: the loop is ~60 lines, every tool is a typed Python function, and keeping it here makes
the guardrails explicit and testable (step cap, call cap via RunTracker, tool allow-list, output truncation,
tool outputs captured for grounding checks).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Any]

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


@dataclass
class AgentResult:
    text: str | None
    steps: list[dict] = field(default_factory=list)       # [{"tool", "args", "ok"}] — shown to the user as a trace
    tool_outputs: list[str] = field(default_factory=list)  # raw JSON strings, used for number verification
    stopped: str | None = None                              # "max_steps" if the loop was cut off


def run_agent(llm, tracker, tier: str, system: str, messages: list[dict], tools: list[ToolSpec],
              *, max_steps: int = 6, max_tool_chars: int = 12000) -> AgentResult:
    by_name = {t.name: t for t in tools}
    schemas = [t.schema() for t in tools]
    convo = [{"role": "system", "content": system}, *messages]
    result = AgentResult(text=None)
    for _ in range(max_steps):
        msg = llm.chat(tracker, tier, convo, schemas)
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            result.text = msg.content or ""
            return result
        convo.append({"role": "assistant", "content": msg.content or "",
                      "tool_calls": [tc.model_dump() if hasattr(tc, "model_dump") else tc for tc in calls]})
        for tc in calls:
            name = tc.function.name
            spec = by_name.get(name)
            try:
                args = json.loads(tc.function.arguments or "{}")
                if spec is None:
                    raise ValueError(f"unknown tool {name}")
                out = spec.fn(**args)
                ok = not (isinstance(out, dict) and "error" in out)
            except Exception as e:  # tool errors go back to the model so it can correct itself
                args, out, ok = {}, {"error": str(e)[:300]}, False
            # ensure_ascii=False keeps "₹992.25" readable; escaped "\u20b9992.25" both confuses the model and glued
            # the escape's last digit onto the number for the grounding check.
            payload = json.dumps(out, default=str, ensure_ascii=False)[:max_tool_chars]
            result.tool_outputs.append(payload)
            result.steps.append({"tool": name, "args": {k: str(v)[:60] for k, v in (args or {}).items()}, "ok": ok})
            tracker.tool_calls.append({"tool": name, "ok": ok})
            convo.append({"role": "tool", "tool_call_id": tc.id, "content": payload})
    # Step budget spent: one final call with tools disabled forces an answer from the evidence gathered so far.
    convo.append({"role": "user", "content": "Stop calling tools. Answer now using only the tool results above, "
                                             "citing evidence ids. Say what is missing if the evidence is insufficient."})
    msg = llm.chat(tracker, tier, convo, None)
    result.text = msg.content or None
    result.stopped = "max_steps"
    return result
