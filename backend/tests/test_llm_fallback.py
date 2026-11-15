"""Model fallback chain: a rate-limited model is skipped (with cooldown) and the next one answers."""
from types import SimpleNamespace as NS

import httpx
import openai

from app.llm import client as llm_client
from app.llm.client import LLM, LLMUnavailable, RunTracker


def _rate_limited(retry: str):
    req = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    return openai.RateLimitError("quota", response=httpx.Response(429, request=req),
                                 body={"error": {"details": [{"retryDelay": retry}]}})


class FakeCompletions:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    def create(self, model, **kw):
        self.calls.append(model)
        b = self.behaviour[model]
        if isinstance(b, Exception):
            raise b
        return NS(usage=NS(prompt_tokens=10, completion_tokens=5), choices=[NS(message=NS(content=b, tool_calls=None))])


def _llm(behaviour, monkeypatch):
    llm = LLM.__new__(LLM)
    llm.s = NS(llm_model_fast="a", llm_model_reasoning="a", llm_fallback_models_fast="b,c", llm_fallback_models_reasoning="b,c",
               llm_max_calls_per_run=40, llm_price_fast_in=0, llm_price_fast_out=0, llm_price_reasoning_in=0, llm_price_reasoning_out=0)
    fake = FakeCompletions(behaviour)
    llm.client = NS(chat=NS(completions=fake))
    monkeypatch.setattr(llm_client, "_cooldown", {})
    monkeypatch.setattr(llm_client, "get_settings", lambda: llm.s)
    return llm, fake


def test_daily_quota_falls_back_and_cools_down(monkeypatch):
    llm, fake = _llm({"a": _rate_limited("18817s"), "b": "from b", "c": "from c"}, monkeypatch)
    t = RunTracker("test")
    assert llm.chat(t, "reasoning", [{"role": "user", "content": "hi"}]).content == "from b"
    assert t.last_model == "b" and fake.calls == ["a", "b"]
    llm.chat(t, "reasoning", [{"role": "user", "content": "again"}])
    assert fake.calls == ["a", "b", "b"]          # 'a' is cooling down: not retried on the next call
    assert llm_client._cooldown["a"] > 0


def test_overloaded_and_retired_models_are_skipped(monkeypatch):
    req = httpx.Request("POST", "https://example.invalid")
    llm, fake = _llm({"a": openai.NotFoundError("gone", response=httpx.Response(404, request=req), body=None),
                      "b": openai.InternalServerError("busy", response=httpx.Response(503, request=req), body=None),
                      "c": "from c"}, monkeypatch)
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
    assert llm.chat(RunTracker("t"), "fast", [{"role": "user", "content": "hi"}]).content == "from c"


def test_all_models_down_raises(monkeypatch):
    llm, _ = _llm({m: _rate_limited("600s") for m in "abc"}, monkeypatch)
    try:
        llm.chat(RunTracker("t"), "fast", [{"role": "user", "content": "hi"}])
        raise AssertionError("expected LLMUnavailable")
    except LLMUnavailable as e:
        assert "rate limited" in str(e)
