"""Token usage and cost tracking for ClimateIQ LLM requests.

- Tracks input/output tokens and calculates approximate USD cost.
- Persists history as JSONL for later summaries.

Notes:
- Token usage is best-effort. If the upstream response does not include token
  counts, cost is recorded as 0.0.
- Pricing changes frequently; override via CostTracker.set_pricing().
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UsageRecord:
    ts: datetime
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost_usd: float
    request_id: str | None = None
    metadata: Mapping[str, Any] | None = None


class CostTracker:
    def __init__(
        self,
        *,
        usage_path: str | None = None,
        max_records: int = 50_000,
    ) -> None:
        self._lock = threading.Lock()
        self._max_records = max(1, int(max_records))
        self._records: list[UsageRecord] = []

        if usage_path is None:
            usage_path = os.getenv("CLIMATEIQ_LLM_USAGE_PATH")
        if usage_path is None:
            usage_path = str(Path(tempfile.gettempdir()) / "climateiq_llm_usage.jsonl")
        self._path = Path(usage_path)

        self._pricing = _default_pricing_table()
        self._load_existing()

    def set_pricing(self, pricing: Mapping[str, Mapping[str, float]]) -> None:
        """Override/extend pricing table.

        Values are USD per 1M tokens.

        Example:
        {"openai": {"gpt-4o-mini_in": 0.15, "gpt-4o-mini_out": 0.6}}
        """
        with self._lock:
            for provider, entries in pricing.items():
                self._pricing.setdefault(provider.lower(), {}).update(dict(entries))

    def record(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        request_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        ts: datetime | None = None,
    ) -> UsageRecord:
        ts = (ts or datetime.now(UTC)).astimezone(UTC)
        total_tokens = None
        if input_tokens is not None or output_tokens is not None:
            total_tokens = int((input_tokens or 0) + (output_tokens or 0))

        cost_usd = self._calculate_cost_usd(provider, model, input_tokens, output_tokens)

        rec = UsageRecord(
            ts=ts,
            provider=provider.lower(),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            request_id=request_id,
            metadata=metadata,
        )

        with self._lock:
            self._records.append(rec)
            if len(self._records) > self._max_records:
                self._records = self._records[-self._max_records :]

        self._persist(rec)
        return rec

    def record_from_litellm_response(
        self,
        *,
        provider: str,
        model: str,
        response: Any,
        request_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        ts: datetime | None = None,
    ) -> UsageRecord:
        usage: Mapping[str, Any] | None = None
        try:
            if isinstance(response, Mapping):
                usage = response.get("usage")
            else:
                usage = getattr(response, "usage", None)
        except Exception:
            usage = None

        in_t = None
        out_t = None
        if isinstance(usage, Mapping):
            in_t = _to_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
            out_t = _to_int(usage.get("completion_tokens") or usage.get("output_tokens"))

        return self.record(
            provider=provider,
            model=model,
            input_tokens=in_t,
            output_tokens=out_t,
            request_id=request_id,
            metadata=metadata,
            ts=ts,
        )

    def get_records(self) -> list[UsageRecord]:
        with self._lock:
            return list(self._records)

    def get_summary(
        self,
        *,
        period: str = "daily",
        now: datetime | None = None,
        tz: tzinfo = UTC,
    ) -> dict[str, Any]:
        now = (now or datetime.now(UTC)).astimezone(tz)
        start = _period_start(now, period)
        end = now

        with self._lock:
            recs = [r for r in self._records if start <= r.ts.astimezone(tz) <= end]

        return _summarize(recs, start=start, end=end, period=period)

    def get_usage_by_provider(
        self,
        *,
        period: str = "daily",
        now: datetime | None = None,
        tz: tzinfo = UTC,
    ) -> dict[str, dict[str, Any]]:
        now = (now or datetime.now(UTC)).astimezone(tz)
        start = _period_start(now, period)
        end = now

        with self._lock:
            recs = [r for r in self._records if start <= r.ts.astimezone(tz) <= end]

        out: dict[str, dict[str, Any]] = {}
        for r in recs:
            row = out.setdefault(r.provider, {"requests": 0, "tokens": 0, "cost_usd": 0.0})
            row["requests"] += 1
            row["tokens"] += int(r.total_tokens or 0)
            row["cost_usd"] += float(r.cost_usd)
        for p in out:
            out[p]["cost_usd"] = round(float(out[p]["cost_usd"]), 6)
        return out

    def _load_existing(self) -> None:
        try:
            if not self._path.exists():
                return
            lines = self._path.read_text(encoding="utf-8").splitlines()
            if not lines:
                return
            for line in lines[-min(len(lines), self._max_records) :]:
                try:
                    obj = json.loads(line)
                    ts = datetime.fromisoformat(obj["ts"]).astimezone(UTC)
                    self._records.append(
                        UsageRecord(
                            ts=ts,
                            provider=str(obj.get("provider") or "unknown"),
                            model=str(obj.get("model") or "unknown"),
                            input_tokens=_to_int(obj.get("input_tokens")),
                            output_tokens=_to_int(obj.get("output_tokens")),
                            total_tokens=_to_int(obj.get("total_tokens")),
                            cost_usd=float(obj.get("cost_usd") or 0.0),
                            request_id=obj.get("request_id"),
                            metadata=obj.get("metadata"),
                        )
                    )
                except Exception:
                    logger.warning("Skipping malformed usage entry")
                    continue
        except Exception:
            logger.warning("Failed to load usage history", exc_info=True)

    def _persist(self, rec: UsageRecord) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            obj = asdict(rec)
            obj["ts"] = rec.ts.astimezone(UTC).isoformat()
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(obj, separators=(",", ":"), ensure_ascii=True) + "\n")
        except Exception:
            logger.warning("Failed to persist usage record", exc_info=True)

    def _calculate_cost_usd(
        self,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> float:
        p = provider.lower().strip()
        if p in {"ollama", "llamacpp", "local"}:
            return 0.0
        if input_tokens is None and output_tokens is None:
            return 0.0

        table = self._pricing.get(p, {})
        in_rate, out_rate = _pricing_for_model(table, model)

        cost = 0.0
        if input_tokens is not None:
            cost += (float(input_tokens) / 1_000_000.0) * in_rate
        if output_tokens is not None:
            cost += (float(output_tokens) / 1_000_000.0) * out_rate
        return float(round(cost, 8))


def _to_int(x: Any) -> int | None:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


def _period_start(now: datetime, period: str) -> datetime:
    p = period.lower().strip()
    if p == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if p == "weekly":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start - timedelta(days=start.weekday())
    if p == "monthly":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError("period must be one of: daily, weekly, monthly")


def _summarize(
    recs: Iterable[UsageRecord],
    *,
    start: datetime,
    end: datetime,
    period: str,
) -> dict[str, Any]:
    cost = 0.0
    reqs = 0
    in_t = 0
    out_t = 0
    total_t = 0
    per_model: dict[tuple[str, str], dict[str, Any]] = {}

    for r in recs:
        reqs += 1
        cost += float(r.cost_usd)
        in_t += int(r.input_tokens or 0)
        out_t += int(r.output_tokens or 0)
        total_t += int(r.total_tokens or 0)

        k = (r.provider, r.model)
        row = per_model.setdefault(k, {"requests": 0, "tokens": 0, "cost_usd": 0.0})
        row["requests"] += 1
        row["tokens"] += int(r.total_tokens or 0)
        row["cost_usd"] += float(r.cost_usd)

    top_models = sorted(
        (
            {
                "provider": k[0],
                "model": k[1],
                "requests": v["requests"],
                "tokens": v["tokens"],
                "cost_usd": round(float(v["cost_usd"]), 6),
            }
            for k, v in per_model.items()
        ),
        key=lambda x: (x["cost_usd"], x["requests"], x["tokens"]),
        reverse=True,
    )

    return {
        "period": period,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "requests": reqs,
        "input_tokens": in_t,
        "output_tokens": out_t,
        "total_tokens": total_t,
        "cost_usd": round(float(cost), 6),
        "top_models": top_models[:10],
    }


def _default_pricing_table() -> dict[str, dict[str, float]]:
    # USD per 1M tokens (approximate defaults).
    return {
        "openai": {
            "default_in": 5.0,
            "default_out": 15.0,
            "gpt-4o-mini_in": 0.15,
            "gpt-4o-mini_out": 0.6,
            "gpt-4o_in": 5.0,
            "gpt-4o_out": 15.0,
            "o1_in": 15.0,
            "o1_out": 60.0,
            "text-embedding-3-small_in": 0.02,
            "text-embedding-3-large_in": 0.13,
        },
        "anthropic": {
            "default_in": 3.0,
            "default_out": 15.0,
            "claude-3-5-sonnet_in": 3.0,
            "claude-3-5-sonnet_out": 15.0,
            "claude-3-5-haiku_in": 0.8,
            "claude-3-5-haiku_out": 4.0,
        },
        "gemini": {
            "default_in": 0.35,
            "default_out": 1.05,
            "gemini-1.5-pro_in": 1.25,
            "gemini-1.5-pro_out": 5.0,
            "gemini-1.5-flash_in": 0.35,
            "gemini-1.5-flash_out": 1.05,
        },
        "grok": {"default_in": 5.0, "default_out": 15.0},
        "ollama": {"default_in": 0.0, "default_out": 0.0},
        "llamacpp": {"default_in": 0.0, "default_out": 0.0},
    }


def _pricing_for_model(table: Mapping[str, float], model: str) -> tuple[float, float]:
    m = model.lower()

    known_prefixes = [
        "gpt-4o-mini",
        "gpt-4o",
        "o1",
        "claude-3-5-sonnet",
        "claude-3-5-haiku",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "text-embedding-3-small",
        "text-embedding-3-large",
    ]

    for prefix in known_prefixes:
        if m.startswith(prefix):
            in_rate = float(table.get(f"{prefix}_in", table.get("default_in", 0.0)))
            out_rate = float(table.get(f"{prefix}_out", table.get("default_out", 0.0)))
            if "embedding" in prefix:
                out_rate = 0.0
            return in_rate, out_rate

    return float(table.get("default_in", 0.0)), float(table.get("default_out", 0.0))
