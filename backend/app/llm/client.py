"""Provider-agnostic LLM access with cost tracking and hard call caps.

Any OpenAI-compatible endpoint works (OpenAI, Groq, OpenRouter, Together, local Ollama) via LLM_BASE_URL,
so swapping providers never touches business logic. When no key is configured, `get_llm()` returns None
and every workflow falls back to its deterministic path — the app stays fully functional.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMBudgetExceeded(RuntimeError):
    pass


@dataclass
class RunTracker:
    workflow: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    models: set[str] = field(default_factory=set)
    tool_calls: list[dict] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)
    status: str = "ok"
    last_model: str | None = None

    def add(self, model: str, tier: str, usage: Any) -> None:
        s = get_settings()
        tin = getattr(usage, "prompt_tokens", 0) or 0
        tout = getattr(usage, "completion_tokens", 0) or 0
        self.tokens_in += tin
        self.tokens_out += tout
        self.calls += 1
        self.models.add(model)
        pin, pout = (
            (s.llm_price_fast_in, s.llm_price_fast_out) if tier == "fast" else (s.llm_price_reasoning_in, s.llm_price_reasoning_out)
        )
        self.cost_usd += tin / 1e6 * pin + tout / 1e6 * pout

    @property
    def duration_ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)


class LLMUnavailable(RuntimeError):
    """Every model in the tier's fallback chain failed or is cooling down."""


# Shared across requests: model -> unix time until which it should not be tried (quota exhausted, overloaded, gone).
_cooldown: dict[str, float] = {}


def _retry_delay_s(err: Exception) -> float | None:
    """Gemini/OpenAI put the provider's suggested wait in the error body or Retry-After header."""
    txt = json.dumps(getattr(err, "body", None) or "") + str(err)
    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', txt)
    if m:
        return float(m.group(1))
    resp = getattr(err, "response", None)
    ra = resp.headers.get("retry-after") if resp is not None else None
    try:
        return float(ra) if ra else None
    except ValueError:
        return None


class LLM:
    """OpenAI-compatible client with a per-tier fallback chain.

    Free-tier quotas are per model (e.g. Gemini's requests-per-day), so when one model is rate-limited (429),
    overloaded (5xx), times out or is retired (404), the next model in the chain answers. A model that hit a
    daily quota is skipped until the provider's suggested retry time instead of being retried on every call.
    """

    def __init__(self) -> None:
        from openai import OpenAI

        s = get_settings()
        self.s = s
        # Retries are handled here (model switching) rather than by the SDK's same-model backoff.
        self.client = OpenAI(api_key=s.llm_api_key, base_url=s.llm_base_url or None, timeout=60, max_retries=0)

    def model_for(self, tier: str) -> str:
        return self.s.llm_model_fast if tier == "fast" else self.s.llm_model_reasoning

    def chain(self, tier: str) -> list[str]:
        extra = self.s.llm_fallback_models_fast if tier == "fast" else self.s.llm_fallback_models_reasoning
        models = [self.model_for(tier)] + [m.strip() for m in extra.split(",") if m.strip()]
        return list(dict.fromkeys(models))

    def _check_budget(self, tracker: RunTracker) -> None:
        if tracker.calls >= self.s.llm_max_calls_per_run:
            raise LLMBudgetExceeded(f"LLM call cap ({self.s.llm_max_calls_per_run}) reached for {tracker.workflow}")

    def _complete(self, tracker: RunTracker, tier: str, **kwargs) -> Any:
        import openai

        self._check_budget(tracker)
        now = time.time()
        candidates = [m for m in self.chain(tier) if _cooldown.get(m, 0) <= now] or self.chain(tier)[-1:]
        errors = []
        for model in candidates:
            for attempt in range(2):
                try:
                    resp = self.client.chat.completions.create(model=model, **kwargs)
                    tracker.add(model, tier, resp.usage)
                    tracker.last_model = model
                    return resp
                except openai.RateLimitError as e:
                    wait = _retry_delay_s(e)
                    if attempt == 0 and wait is not None and wait <= 8:
                        time.sleep(wait)  # short per-minute limit: wait once, same model
                        continue
                    _cooldown[model] = time.time() + min(wait or 60, 24 * 3600)
                    errors.append(f"{model}: rate limited")
                except openai.NotFoundError:
                    _cooldown[model] = time.time() + 24 * 3600
                    errors.append(f"{model}: not available")
                except (openai.InternalServerError, openai.APIConnectionError, openai.APITimeoutError) as e:
                    if attempt == 0:
                        time.sleep(1.0)
                        continue
                    _cooldown[model] = time.time() + 60
                    errors.append(f"{model}: {type(e).__name__}")
                break
            log.warning("LLM %s tier: %s failed, trying next model", tier, model)
        raise LLMUnavailable("; ".join(errors) or "no model available")

    def structured(self, tracker: RunTracker, tier: str, system: str, user: str, schema: type[T]) -> T | None:
        """JSON-mode completion validated with Pydantic. Returns None if the model output is invalid twice."""
        schema_hint = json.dumps(schema.model_json_schema())[:4000]
        messages = [
            {"role": "system", "content": f"{system}\n\nRespond with a single JSON object matching this JSON schema:\n{schema_hint}"},
            {"role": "user", "content": user},
        ]
        for attempt in range(2):
            resp = self._complete(tracker, tier, messages=messages, temperature=0, response_format={"type": "json_object"})
            raw = resp.choices[0].message.content or "{}"
            try:
                return schema.model_validate_json(raw)
            except ValidationError as e:
                log.warning("Structured output invalid (attempt %d): %s", attempt + 1, str(e)[:300])
                messages += [{"role": "assistant", "content": raw},
                             {"role": "user", "content": f"That JSON failed validation: {str(e)[:800]}. Return corrected JSON only."}]
        return None

    def chat(self, tracker: RunTracker, tier: str, messages: list[dict], tools: list[dict] | None = None) -> Any:
        resp = self._complete(tracker, tier, messages=messages, tools=tools or None, temperature=0.1)
        return resp.choices[0].message

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self.s.llm_embedding_model
        if not model:
            raise RuntimeError("embeddings disabled")
        if _cooldown.get(model, 0) > time.time():
            raise RuntimeError("embedding model cooling down")
        out: list[list[float]] = []
        try:
            for i in range(0, len(texts), 96):
                r = self.client.embeddings.create(model=model, input=[t[:6000] for t in texts[i : i + 96]])
                out.extend(d.embedding for d in r.data)
        except Exception as e:  # keyword search still works; don't hammer a limited endpoint
            _cooldown[model] = time.time() + min(_retry_delay_s(e) or 300, 24 * 3600)
            raise
        return out

    @staticmethod
    def status() -> dict:
        now = time.time()
        return {m: round(t - now) for m, t in _cooldown.items() if t > now}


_llm: LLM | None = None


def get_llm() -> LLM | None:
    global _llm
    if not get_settings().llm_enabled:
        return None
    if _llm is None:
        _llm = LLM()
    return _llm


@contextmanager
def tracked_run(db, workflow: str):
    """Persist one AgentRun row per workflow invocation (tokens, cost, latency — never document text)."""
    from app.models import AgentRun

    tracker = RunTracker(workflow)
    try:
        yield tracker
    except Exception:
        tracker.status = "error"
        raise
    finally:
        s = get_settings()
        model = ",".join(sorted(tracker.models)) or ("deterministic" if not s.llm_enabled else "none-called")
        db.add(AgentRun(workflow=workflow, model=model[:64], tokens_in=tracker.tokens_in, tokens_out=tracker.tokens_out,
                        llm_calls=tracker.calls, estimated_cost_usd=round(tracker.cost_usd, 6),
                        duration_ms=tracker.duration_ms, status=tracker.status, trace_id=tracker.trace_id,
                        tool_calls=tracker.tool_calls[:50]))
        db.commit()
