"""Chat assistant: reasoning text, unknown zone ids, websocket validation; settings: comfort defaults."""

import uuid
from typing import Any, ClassVar

import pytest
from fastapi import WebSocketDisconnect
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import backend.api.routes.chat as chat
from backend.integrations.llm.provider import strip_reasoning
from backend.models.database import (
    Conversation,
    UserDirective,
    get_session_maker,
)
from backend.tests.scenario.harness import House


def test_strip_reasoning() -> None:
    assert strip_reasoning("<think>draft 99</think>\nFinal answer") == "Final answer"
    assert strip_reasoning("a<think>x</think>b<think>y</think>c") == "abc"
    # An unterminated block (ran out of tokens while thinking) is dropped too.
    assert strip_reasoning("answer<think>never closed") == "answer"
    # Untouched when there is nothing to strip.
    assert strip_reasoning("plain  ") == "plain  "


class _ThinkLLM:
    provider = "ollama"

    async def chat(self, **kw: Any) -> dict[str, Any]:
        return {
            "content": "<think>user says X</think>\n"
            '[{"directive":"Keep bedroom cool at night","category":"preference"}]'
        }


async def test_directive_extraction_survives_reasoning_text(
    house: House, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _llm(_db: Any) -> _ThinkLLM:
        return _ThinkLLM()

    monkeypatch.setattr(chat, "get_llm_provider", _llm)
    async with get_session_maker()() as db:
        conversation = Conversation(session_id="s", user_message="u", assistant_response="a")
        db.add(conversation)
        await db.commit()
        await chat._extract_directives("Keep bedroom cool at night.", "ok", conversation.id, db, {})
    async with get_session_maker()() as db:
        saved = (await db.execute(select(UserDirective))).scalars().all()
    assert len(saved) == 1


async def test_unknown_zone_id_is_not_a_house_wide_override(house: House) -> None:
    house.at(2026, 1, 12, 10, 0)
    await house.add_zone("Real", 68)
    async with get_session_maker()() as db:
        error = await chat._command_set_zone_temp(db, uuid.uuid4(), 24.0, "chat")
        bad_format = await chat._command_set_zone_temp(db, "kitchen", 24.0, "chat")
    assert error == "Zone not found."
    assert bad_format == "Zone not found."
    # ...and nothing was written to the thermostat.
    assert not [c for c in house.ha.calls if c[1] in ("set_temperature", "set_hvac_mode")]


class _FakeWS:
    def __init__(self, msgs: list[Any]) -> None:
        self.msgs = list(msgs)
        self.sent: list[dict[str, Any]] = []

    async def accept(self) -> None:
        return None

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    async def receive_json(self) -> Any:
        if not self.msgs:
            raise WebSocketDisconnect()
        return self.msgs.pop(0)


class _Echo:
    provider = "ollama"
    seen: ClassVar[list[Any]] = []

    async def chat(self, messages: Any = None, **kw: Any) -> dict[str, Any]:
        _Echo.seen.append(messages)
        return {"content": "ok"}


async def test_chat_websocket_validates_frames(
    house: House, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _llm(_db: Any) -> _Echo:
        return _Echo()

    monkeypatch.setattr(chat, "get_llm_provider", _llm)
    _Echo.seen = []
    ws = _FakeWS(
        [
            ["not", "a", "dict"],  # malformed frame must not kill the socket
            {"type": "message", "content": "x" * 2_000_000},  # over the REST cap
            {"type": "message", "content": ""},
            {"type": "message", "content": 123},
            {"type": "ping"},
        ]
    )
    await chat.chat_websocket(ws)  # type: ignore[arg-type]
    types = [m["type"] for m in ws.sent]
    assert types.count("error") == 4, types
    assert "pong" in types, "the socket must survive the bad frames"
    assert not _Echo.seen, "nothing invalid may reach the LLM"
    assert all("object has no attribute" not in str(m.get("message")) for m in ws.sent)


async def test_comfort_defaults_validation_applies_only_to_changed_values(
    house: House, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.api.main as m
    from backend.api.main import app
    from backend.api.middleware import RateLimitMiddleware
    from backend.api.routes.settings import _upsert_kv
    from backend.models.database import close_db, init_db

    monkeypatch.setattr(m.app_state, "zone_manager", None)
    await close_db()
    await init_db()
    node = getattr(app, "middleware_stack", None)
    while node is not None:  # the limiter's budget is shared by the whole test process
        if isinstance(node, RateLimitMiddleware):
            node._requests.clear()
            break
        node = getattr(node, "app", None)

    def general(lo: float, hi: float, unit: str = "C") -> dict[str, Any]:
        return {
            "system_name": "x",
            "timezone": "UTC",
            "temperature_unit": unit,
            "default_comfort_temp_min": lo,
            "default_comfort_temp_max": hi,
            "default_humidity_min": 30,
            "default_humidity_max": 60,
            "energy_cost_per_kwh": 0.12,
            "currency": "USD",
            "max_temp_offset_f": 0,
        }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:

        async def put(payload: dict[str, Any]) -> int:
            return (await c.put("/api/v1/settings", json=payload)).status_code

        # Normal values, including the exact edges the page can produce.
        assert await put(general(20, 24)) == 200
        assert await put(general(4.4, 37.8)) == 200
        assert await put(general(-10, 50)) == 200
        # A changed value that is out of range, a blank-°F zero, NaN-ish, wrong order.
        assert await put(general(-17.78, 24)) == 422
        assert await put(general(20, 75)) == 422
        assert await put(general(26, 20)) == 422
        # A partial update must respect the stored counterpart.
        assert await put(general(20, 24)) == 200
        assert (
            await c.put("/api/v1/settings", json={"default_comfort_temp_min": 30})
        ).status_code == 422

        # A LEGACY out-of-range stored value that the General card merely echoes back
        # must not make every other setting fail to save.
        async with get_session_maker()() as db:
            await _upsert_kv(db, "default_comfort_temp_min", 75.0)
            await _upsert_kv(db, "default_comfort_temp_max", 80.0)
            await db.commit()
        assert await put(general(75.0, 80.0)) == 200
        assert await put(general(75.02, 79.98)) == 200  # display rounding
        # ...but actually editing it to another bad value is still refused.
        assert await put(general(60.0, 80.0)) == 422
