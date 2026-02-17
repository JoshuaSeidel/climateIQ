"""Prompt templates for ClimateIQ.

Keep prompts short and structured to reduce tokens.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

CLIMATE_ASSISTANT_SYSTEM_PROMPT = (
    "You are ClimateIQ, a building climate assistant. "
    "Be concise, practical, and safety-aware. "
    "Use tools for live data or control actions. "
    "Do not invent readings or device state. "
    "Ask for missing inputs."
)


def format_zones_context(
    *,
    zones: Sequence[Mapping[str, Any]],
    max_zones: int = 10,
    max_sensors_per_zone: int = 8,
) -> str:
    """Compact zones/sensors/readings context.

    Zone dicts are flexible; common keys: id, name, setpoint_c, mode, hvac_state,
    sensors=[{id,type,value,unit,ts}].
    """

    lines = ["ZONES:"]
    for z in list(zones)[: max(0, int(max_zones))]:
        zid = str(z.get("id") or "")
        name = str(z.get("name") or zid)
        sp = z.get("setpoint_c")
        mode = z.get("mode")
        hvac = z.get("hvac_state")
        lines.append(
            f"- {zid} | {name} | spC={_fmt_num(sp)} | mode={mode or '?'} | hvac={hvac or '?'}"
        )

        sensors = z.get("sensors") or []
        for s in list(sensors)[: max(0, int(max_sensors_per_zone))]:
            sid = str(s.get("id") or "")
            stype = str(s.get("type") or "")
            val = s.get("value")
            unit = s.get("unit") or ""
            ts = s.get("ts") or s.get("timestamp") or "?"
            lines.append(f"  * {sid}:{stype}={_fmt_num(val)}{unit} @{ts}")

    return "\n".join(lines)


def format_patterns_context(
    *,
    patterns: Sequence[Mapping[str, Any]] | None = None,
    max_items: int = 10,
) -> str:
    if not patterns:
        return "PATTERNS: none"
    lines = ["PATTERNS:"]
    for p in list(patterns)[: max(0, int(max_items))]:
        kind = p.get("type") or p.get("kind") or "pattern"
        desc = (p.get("description") or p.get("desc") or "").strip()
        score = p.get("score")
        lines.append(f"- {kind} | score={_fmt_num(score)} | {desc}")
    return "\n".join(lines)


def decision_request_prompt(*, goal: str, constraints: Sequence[str] | None = None) -> str:
    c = " | ".join([str(x).strip() for x in (constraints or []) if str(x).strip()])
    if c:
        return f"GOAL: {goal}\nCONSTRAINTS: {c}\nReturn: plan + required tool calls."
    return f"GOAL: {goal}\nReturn: plan + required tool calls."


def integrate_user_feedback_prompt(*, feedback: str) -> str:
    fb = (feedback or "").strip()
    if not fb:
        return "USER_FEEDBACK: none"
    return f"USER_FEEDBACK:\n{fb}\nIncorporate this in your next reply."


def compact_message_history(
    messages: Sequence[Mapping[str, Any]], *, max_messages: int = 20
) -> str:
    kept = list(messages)[-max(1, int(max_messages)) :]
    lines = ["HISTORY:"]
    for m in kept:
        role = str(m.get("role") or "").upper()
        content = str(m.get("content") or "").strip()
        if len(content) > 600:
            content = content[:600] + "..."
        lines.append(f"- {role}: {content}")
    return "\n".join(lines)


def _fmt_num(x: Any) -> str:
    try:
        if x is None:
            return "?"
        if isinstance(x, bool):
            return "true" if x else "false"
        if isinstance(x, int):
            return str(x)
        if isinstance(x, float):
            return str(round(x, 2))
        return str(x)
    except Exception:
        return "?"
