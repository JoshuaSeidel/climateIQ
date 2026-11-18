"""Gemini support: Gemini 3 parameters, JSON mode, free-tier rate cap and quota pause."""

from typing import Any, ClassVar

import pytest

from backend.integrations.llm import provider as prov


class _FakeLiteLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def completion(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if self.error is not None:
            raise self.error
        return {"choices": [{"message": {"role": "assistant", "content": '{"action": "hold"}'}}]}


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeLiteLLM:
    fake = _FakeLiteLLM()
    monkeypatch.setattr(prov, "_require_litellm", lambda: fake)
    monkeypatch.setattr(prov, "_PROVIDER_COOLDOWN_UNTIL", {})
    monkeypatch.setattr(prov, "_SHARED_LIMITERS", {})
    return fake


def _provider(model: str, **settings: Any) -> prov.ClimateIQLLMProvider:
    return prov.ClimateIQLLMProvider(
        primary=prov.ProviderSettings(
            provider="gemini", api_key="k", default_model=model, **settings
        )
    )


def test_gemini3_gets_default_temperature_and_thinking_headroom(fake: _FakeLiteLLM) -> None:
    _provider("gemini-3.5-flash-lite").chat(
        messages=[{"role": "user", "content": "hi"}], max_tokens=250, temperature=0.1
    )
    call = fake.calls[0]
    assert "temperature" not in call, "Gemini 3 must keep its default temperature"
    assert call["max_tokens"] >= 2048, "250 tokens is spent on hidden thinking before the JSON"


def test_other_models_keep_the_requested_temperature_and_budget(fake: _FakeLiteLLM) -> None:
    _provider("gemini-2.0-flash").chat(
        messages=[{"role": "user", "content": "hi"}], max_tokens=250, temperature=0.1
    )
    assert fake.calls[0]["temperature"] == 0.1
    assert fake.calls[0]["max_tokens"] == 250


def test_json_mode_is_requested_only_for_gemini(fake: _FakeLiteLLM) -> None:
    _provider("gemini-3.5-flash-lite").chat(
        messages=[{"role": "user", "content": "hi"}], json_mode=True
    )
    assert fake.calls[0]["response_format"] == {"type": "json_object"}
    assert "json_mode" not in fake.calls[0]
    ollama = prov.ClimateIQLLMProvider(
        primary=prov.ProviderSettings(provider="ollama", base_url="http://x", default_model="q")
    )
    ollama.chat(messages=[{"role": "user", "content": "hi"}], json_mode=True)
    assert "response_format" not in fake.calls[1] and "json_mode" not in fake.calls[1]


def test_gemini_rpm_is_capped_for_the_free_tier() -> None:
    assert _provider("gemini-3.5-flash-lite", rpm=60).primary.rpm == 10
    assert _provider("gemini-3.5-flash-lite", rpm=5).primary.rpm == 5


def test_quota_error_pauses_the_provider_instead_of_retrying_every_call(
    fake: _FakeLiteLLM,
) -> None:
    fake.error = RuntimeError("429 RESOURCE_EXHAUSTED: Quota exceeded. Please retry in 23.4s")
    first = _provider("gemini-3.5-flash-lite")
    first.chat(messages=[{"role": "user", "content": "hi"}])
    assert len(fake.calls) == 1
    # A NEW provider object (the advisor builds one per call) must honour the pause.
    second = _provider("gemini-3.5-flash-lite")
    second.chat(messages=[{"role": "user", "content": "hi"}])
    assert len(fake.calls) == 1, "no further request while the quota pause is active"
    (until,) = prov._PROVIDER_COOLDOWN_UNTIL.values()
    pause = until - prov.time.monotonic()
    assert 20 < pause <= 25  # Google's own "retry in 23.4s" hint (+1s)


def test_quota_cooldown_parsing() -> None:
    assert prov._quota_cooldown_s(RuntimeError("something else")) is None
    assert prov._quota_cooldown_s(RuntimeError("429 Too Many Requests")) == 60.0
    assert prov._quota_cooldown_s(RuntimeError("quota exceeded for requests per day")) == 3600.0
    assert prov._quota_cooldown_s(RuntimeError("RESOURCE_EXHAUSTED retry in 2s")) == 5.0
    assert prov._quota_cooldown_s(RuntimeError("RESOURCE_EXHAUSTED retry in 9000s")) == 300.0


def test_quota_detection_ignores_unrelated_errors_and_reads_ms_hints() -> None:
    # 403 "quota project" and ids containing 1429 are NOT rate limits.
    assert prov._quota_cooldown_s(RuntimeError("403 PERMISSION_DENIED: requires a quota project")) is None
    assert prov._quota_cooldown_s(RuntimeError("bad request at offset 1429")) is None
    assert prov._quota_cooldown_s(RuntimeError("RESOURCE_EXHAUSTED retry in 1500ms")) == 5.0
    assert prov._quota_cooldown_s(RuntimeError("RESOURCE_EXHAUSTED retryDelay: 30s")) == 31.0
    assert prov._quota_cooldown_s(RuntimeError("PerMinute quota exceeded")) == 60.0


def test_pause_is_per_model_so_another_model_still_works(fake: _FakeLiteLLM) -> None:
    fake.error = RuntimeError("429 RESOURCE_EXHAUSTED retry in 30s")
    _provider("gemini-3.5-flash-lite").chat(messages=[{"role": "user", "content": "hi"}])
    fake.error = None
    _provider("gemini-2.5-flash").chat(messages=[{"role": "user", "content": "hi"}])
    assert len(fake.calls) == 2


def test_chat_provider_pauses_after_quota_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setattr(prov, "_PROVIDER_COOLDOWN_UNTIL", {})
    calls: list[dict[str, Any]] = []

    class _LL:
        async def acompletion(self, **kw: Any) -> Any:
            calls.append(kw)
            raise RuntimeError("429 RESOURCE_EXHAUSTED retry in 30s")

    monkeypatch.setattr(prov, "_require_litellm", lambda: _LL())
    provider = prov.LLMProvider("gemini", api_key="k", model="gemini-3.5-flash-lite")
    for _ in range(2):
        with pytest.raises(Exception):  # noqa: B017
            asyncio.run(provider.chat(messages=[{"role": "user", "content": "hi"}]))
    assert len(calls) == 1


def test_chat_provider_drops_temperature_for_gemini3(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    calls: list[dict[str, Any]] = []

    class _Resp:
        class _Choice:
            class message:
                content = "ok"
                tool_calls = None

        choices: ClassVar[list[Any]] = [_Choice()]

    class _LL:
        async def acompletion(self, **kw: Any) -> Any:
            calls.append(kw)
            return _Resp()

    monkeypatch.setattr(prov, "_require_litellm", lambda: _LL())
    provider = prov.LLMProvider("gemini", api_key="k", model="gemini-3.5-flash-lite")
    asyncio.run(provider.chat(messages=[{"role": "user", "content": "hi"}]))
    assert "temperature" not in calls[0]
    assert calls[0]["max_tokens"] >= 2048
