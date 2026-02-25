"""LLM-driven climate control advisor.

This module is the AI brain of ClimateIQ's thermostat control loop. On each
maintenance tick (when the formula computes a non-zero offset), it is asked:

  "Should we apply this setpoint change right now, modify it, or wait?"

It assembles rich context from:
  - Current zone temps vs thermostat reading vs schedule target
  - 2h of 5-min temperature trend data from TimescaleDB
  - Per-zone thermal profile (heating/cooling rates, response lag, overshoot)
  - Occupancy patterns (presence + lux from sensor history)
  - Current outdoor weather conditions
  - Recent thermostat actions for continuity

The LLM returns a JSON decision which is always passed through SafetyProtocol
before any thermostat call is made.  If the LLM is unavailable or returns
invalid JSON, the formula result is used transparently as a fallback.

Disable AI decision-making at runtime via the ``ai_advisor_enabled`` system
setting (persisted in system_settings, toggled from the Settings UI).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_LLM_CACHE_MINUTES = 10       # max age of a cached decision before re-querying LLM
_LLM_CACHE_TEMP_DELTA_C = 5.0 / 9.0  # 1°F — invalidate cache if zone avg changes by this much
_MAX_WAIT_MINUTES = 30        # LLM cannot request a wait longer than this
_LLM_MAX_TOKENS = 250
_LLM_TEMPERATURE = 0.1        # low temp → deterministic, factual decisions
_SAFETY_ABS_MIN_C = 12.78     # 55°F — physical safety floor
_SAFETY_ABS_MAX_C = 32.22     # 90°F — physical safety ceiling


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TrendPoint:
    bucket: datetime
    avg_temp_c: float


@dataclass
class TrendData:
    recent_5min: list[TrendPoint] = field(default_factory=list)   # last 2h, 5-min buckets
    recent_hourly: list[TrendPoint] = field(default_factory=list)  # last 48h, hourly buckets
    rate_c_per_hour: float = 0.0                                   # linear regression, last 30 min
    time_to_target_hours: float | None = None                      # None if not converging
    last_actions: list[dict[str, Any]] = field(default_factory=list)  # last 5 set_temp actions


@dataclass
class AdvisorDecision:
    action: Literal["adjust", "hold", "wait"]
    setpoint_c: float           # always present — formula value if hold/wait
    wait_until: datetime | None  # only when action=="wait"
    reasoning: str
    from_llm: bool              # False = formula passthrough (LLM skipped or failed)


@dataclass
class _AdvisorState:
    """Cached decision for one schedule."""
    decision: AdvisorDecision
    decided_at: datetime
    avg_c_at_decision: float


# Module-level cache: schedule_id → _AdvisorState
_state: dict[str, _AdvisorState] = {}


def clear_advisor_cache() -> None:
    """Clear all cached advisor decisions (call on drift detection)."""
    _state.clear()
    logger.debug("ClimateAdvisor: cache cleared")


# ---------------------------------------------------------------------------
# LLM provider construction
# ---------------------------------------------------------------------------

async def _build_llm_provider(db: Any, settings: Any) -> Any:
    """Build a ClimateIQLLMProvider with updated defaults.

    Priority:
    1. SystemConfig.llm_settings (user-configured provider/model)
    2. First available API key in settings (Anthropic → OpenAI → Gemini → Grok → Ollama)
    3. Hard-coded defaults: Anthropic claude-sonnet-4-6, fallback OpenAI gpt-4o-mini
    """
    from backend.integrations.llm.provider import ClimateIQLLMProvider, ProviderSettings
    from backend.models.database import SystemConfig

    primary_provider = "anthropic"
    primary_model = "claude-sonnet-4-6"
    secondary_provider = "openai"
    secondary_model = "gpt-4o-mini"

    # Read user-configured LLM override from SystemConfig
    try:
        result = await db.execute(
            SystemConfig.__table__.select().where(SystemConfig.id == 1)
        )
        row = result.first()
        llm_settings = dict(row.llm_settings or {}) if row else {}
        if llm_settings.get("provider"):
            primary_provider = llm_settings["provider"]
        if llm_settings.get("model"):
            primary_model = llm_settings["model"]
    except Exception:
        pass

    def _api_key(provider: str) -> str | None:
        mapping = {
            "anthropic": getattr(settings, "anthropic_api_key", ""),
            "openai": getattr(settings, "openai_api_key", ""),
            "gemini": getattr(settings, "gemini_api_key", ""),
            "grok": getattr(settings, "grok_api_key", ""),
            "ollama": None,
        }
        val = mapping.get(provider, "")
        return val or None

    primary = ProviderSettings(
        provider=primary_provider,
        api_key=_api_key(primary_provider),
        default_model=primary_model,
    )
    secondary = ProviderSettings(
        provider=secondary_provider,
        api_key=_api_key(secondary_provider),
        default_model=secondary_model,
    )
    return ClimateIQLLMProvider(primary=primary, secondary=secondary)


# ---------------------------------------------------------------------------
# Trend data queries
# ---------------------------------------------------------------------------

async def _get_trend_data(
    db: Any,
    sensor_ids: list[Any],
    zone_ids: list[Any],
    desired_temp_c: float,
) -> TrendData:
    """Query TimescaleDB continuous aggregates for trend analysis."""
    from sqlalchemy import text

    if not sensor_ids:
        return TrendData()

    sensor_id_strs = [str(sid) for sid in sensor_ids]
    zone_id_strs = [str(zid) for zid in zone_ids]

    # ── 5-min recent trend (last 2h) ────────────────────────────────────────
    recent_5min: list[TrendPoint] = []
    try:
        rows = await db.execute(
            text(
                "SELECT bucket, AVG(avg_temperature_c)::float AS avg_temp_c "
                "FROM sensor_readings_5min "
                "WHERE sensor_id = ANY(:sensor_ids) "
                "  AND bucket >= NOW() - INTERVAL '2 hours' "
                "GROUP BY bucket ORDER BY bucket ASC"
            ).bindparams(sensor_ids=sensor_id_strs)
        )
        for row in rows.fetchall():
            if row.avg_temp_c is not None and -40 <= row.avg_temp_c <= 60:
                recent_5min.append(TrendPoint(bucket=row.bucket, avg_temp_c=row.avg_temp_c))
    except Exception as exc:
        logger.debug("ClimateAdvisor: trend 5min query failed: %s", exc)

    # ── Hourly trend (last 48h) ──────────────────────────────────────────────
    recent_hourly: list[TrendPoint] = []
    try:
        rows = await db.execute(
            text(
                "SELECT bucket, AVG(avg_temperature_c)::float AS avg_temp_c "
                "FROM sensor_readings_hourly "
                "WHERE sensor_id = ANY(:sensor_ids) "
                "  AND bucket >= NOW() - INTERVAL '48 hours' "
                "GROUP BY bucket ORDER BY bucket ASC"
            ).bindparams(sensor_ids=sensor_id_strs)
        )
        for row in rows.fetchall():
            if row.avg_temp_c is not None and -40 <= row.avg_temp_c <= 60:
                recent_hourly.append(TrendPoint(bucket=row.bucket, avg_temp_c=row.avg_temp_c))
    except Exception as exc:
        logger.debug("ClimateAdvisor: trend hourly query failed: %s", exc)

    # ── Rate of change from last 30 min of 5-min data ───────────────────────
    rate_c_per_hour = 0.0
    cutoff_30m = datetime.now(UTC) - timedelta(minutes=30)
    recent_window = [p for p in recent_5min if p.bucket >= cutoff_30m]
    if len(recent_window) >= 2:
        times_h = [(p.bucket - recent_window[0].bucket).total_seconds() / 3600 for p in recent_window]
        temps_c = [p.avg_temp_c for p in recent_window]
        n = len(times_h)
        mean_t = sum(times_h) / n
        mean_T = sum(temps_c) / n
        num = sum((times_h[i] - mean_t) * (temps_c[i] - mean_T) for i in range(n))
        den = sum((times_h[i] - mean_t) ** 2 for i in range(n))
        if abs(den) > 1e-9:
            rate_c_per_hour = num / den

    # ── Time to target ───────────────────────────────────────────────────────
    time_to_target_hours: float | None = None
    if recent_5min and abs(rate_c_per_hour) > 0.05:
        current_avg = recent_5min[-1].avg_temp_c
        gap_c = desired_temp_c - current_avg
        # rate converges if gap and rate have the same sign
        if gap_c * rate_c_per_hour > 0:
            time_to_target_hours = abs(gap_c / rate_c_per_hour)

    # ── Recent device actions ────────────────────────────────────────────────
    last_actions: list[dict[str, Any]] = []
    if zone_id_strs:
        try:
            rows = await db.execute(
                text(
                    "SELECT created_at, parameters, reasoning "
                    "FROM device_actions "
                    "WHERE zone_id = ANY(:zone_ids) "
                    "  AND action_type = 'set_temperature' "
                    "  AND created_at >= NOW() - INTERVAL '4 hours' "
                    "ORDER BY created_at DESC LIMIT 5"
                ).bindparams(zone_ids=zone_id_strs)
            )
            for row in rows.fetchall():
                last_actions.append({
                    "at": row.created_at.isoformat() if row.created_at else "",
                    "params": row.parameters or {},
                    "reason": row.reasoning or "",
                })
        except Exception as exc:
            logger.debug("ClimateAdvisor: device actions query failed: %s", exc)

    return TrendData(
        recent_5min=recent_5min,
        recent_hourly=recent_hourly,
        rate_c_per_hour=rate_c_per_hour,
        time_to_target_hours=time_to_target_hours,
        last_actions=last_actions,
    )


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _build_prompt(
    *,
    hvac_mode: str,
    desired_temp_c: float,
    avg_temp_c: float,
    thermostat_c: float | None,
    current_setpoint_c: float,
    formula_adjusted_c: float,
    zone_names: str | None,
    trend: TrendData,
    thermal_profile: dict[str, Any],
    outdoor_temp_c: float | None,
    outdoor_condition: str,
    last_setpoint_changed_minutes: int,
    temp_unit: str,
) -> str:
    """Assemble the full LLM decision prompt."""
    target_f = round(_c_to_f(desired_temp_c))
    avg_f = round(_c_to_f(avg_temp_c), 1)
    formula_f = round(_c_to_f(formula_adjusted_c))
    setpoint_f = round(_c_to_f(current_setpoint_c))
    thermostat_f = round(_c_to_f(thermostat_c), 1) if thermostat_c is not None else "unknown"
    delta_f = round(avg_f - target_f, 1)
    delta_sign = f"{delta_f:+.1f}"
    loc_delta = (
        round(_c_to_f(avg_temp_c) - _c_to_f(thermostat_c), 1)
        if thermostat_c is not None else "unknown"
    )
    rate_fph = round(trend.rate_c_per_hour * 9 / 5, 1) if trend.rate_c_per_hour else 0.0
    direction = "warming" if rate_fph > 0 else "cooling" if rate_fph < 0 else "stable"

    if trend.time_to_target_hours is not None:
        eta_h = trend.time_to_target_hours
        if eta_h < 1:
            eta_str = f"~{int(eta_h * 60)} min"
        else:
            eta_str = f"~{eta_h:.1f} hours"
    else:
        eta_str = "unknown (not converging at current rate)"

    # Trend table
    trend_rows = ""
    if trend.recent_5min:
        shown = trend.recent_5min[-12:]  # last hour at most
        now_utc = datetime.now(UTC)
        for pt in shown:
            mins_ago = int((now_utc - pt.bucket).total_seconds() / 60)
            trend_rows += f"  -{mins_ago:3d}m   {_c_to_f(pt.avg_temp_c):.1f}°F\n"
    if not trend_rows:
        trend_rows = "  (no recent data)\n"

    # Thermal profile section
    profile_lines = ""
    if thermal_profile:
        hr = thermal_profile.get("heating_rate_c_per_hour")
        cr = thermal_profile.get("cooling_rate_c_per_hour")
        lag = thermal_profile.get("response_lag_minutes")
        overshoot = thermal_profile.get("typical_overshoot_c")
        days = thermal_profile.get("data_days", 0)
        nap = thermal_profile.get("nap_detected", False)
        occ_by_hour: dict[str, float] = thermal_profile.get("occupancy_score_by_hour", {})
        current_hour = datetime.now(UTC).hour
        occ_score = occ_by_hour.get(str(current_hour), 0.0)
        occ_status = "likely occupied" if occ_score >= 0.5 else "likely unoccupied"
        if hr is not None:
            profile_lines += f"Heating rate:       {hr * 9/5:+.1f}°F/hour\n"
        if cr is not None:
            profile_lines += f"Cooling rate:       {cr * 9/5:+.1f}°F/hour\n"
        if lag is not None:
            profile_lines += f"Response lag:       ~{lag:.0f} min (setpoint change → zone response)\n"
        if overshoot is not None:
            profile_lines += f"Typical overshoot:  +{overshoot * 9/5:.1f}°F after heating cycles\n"
        profile_lines += f"Occupancy now:      {occ_status} (score {occ_score:.0%} for this hour)\n"
        if nap:
            nap_hours = thermal_profile.get("typical_nap_hours", [])
            profile_lines += f"Nap pattern:        detected ({', '.join(f'{h}:00' for h in nap_hours)})\n"
        label = f"learned from {days} days" if days else "building profile"
        profile_header = f"─── ZONE THERMAL PROFILE ({label}) ──────────────────────────"
    else:
        profile_header = "─── ZONE THERMAL PROFILE (not yet available) ─────────────"
        profile_lines = "  Profile will populate after the first analytics run (every 4 hours).\n"

    # Outdoor conditions
    if outdoor_temp_c is not None:
        outdoor_str = f"{_c_to_f(outdoor_temp_c):.0f}°F  {outdoor_condition}"
    else:
        outdoor_str = "unavailable"

    # Recent actions
    actions_str = ""
    if trend.last_actions:
        for a in trend.last_actions:
            params = a.get("params", {})
            temp = params.get("temperature", "?")
            reason = a.get("reason", "") or "—"
            actions_str += f"  {a['at'][:16]}  →  {temp}°{'F' if temp_unit == 'F' else 'C'}  ({reason[:60]})\n"
    else:
        actions_str = "  (no recent set_temperature actions)\n"

    zone_label = f"  Zones: {zone_names}" if zone_names else ""

    # Compute whether the HVAC is actually running right now.
    # The thermostat satisfies itself against its own sensor, not zone sensors.
    # Heat runs only while thermostat reading < setpoint; cool runs only while
    # thermostat reading > setpoint.  If the relationship is inverted the HVAC
    # is idle — any trend rate in the data reflects recent history, not current.
    if thermostat_c is not None:
        t_f = float(thermostat_f)
        if "heat" in hvac_mode:
            if t_f < setpoint_f:
                hvac_running = f"YES — thermostat ({thermostat_f}°F) is below setpoint ({setpoint_f}°F), heat is firing"
            else:
                hvac_running = (
                    f"NO — thermostat ({thermostat_f}°F) is at or above setpoint ({setpoint_f}°F); "
                    f"heat will not run until setpoint > {thermostat_f}°F"
                )
        elif "cool" in hvac_mode:
            if t_f > setpoint_f:
                hvac_running = f"YES — thermostat ({thermostat_f}°F) is above setpoint ({setpoint_f}°F), AC is firing"
            else:
                hvac_running = (
                    f"NO — thermostat ({thermostat_f}°F) is at or below setpoint ({setpoint_f}°F); "
                    f"AC will not run until setpoint < {thermostat_f}°F"
                )
        else:
            hvac_running = "unknown (mode is off or heat_cool)"
    else:
        hvac_running = "unknown (thermostat reading unavailable)"

    return f"""─── CURRENT STATE ────────────────────────────────────────────────
HVAC mode:          {hvac_mode or 'unknown'}
Schedule target:    {target_f}°F ({desired_temp_c:.1f}°C)
Zone average:       {avg_f}°F ({delta_sign}°F vs target){zone_label}
Thermostat reads:   {thermostat_f}°F  (location offset from zones: {loc_delta}°F)
Current setpoint:   {setpoint_f}°F  (last changed {last_setpoint_changed_minutes} min ago)
HVAC currently:     {hvac_running}
Formula says:       {formula_f}°F  ({formula_f - target_f:+.0f}°F vs target)
Outdoor now:        {outdoor_str}

─── TEMPERATURE TREND (5-min, last 1h) ────────────────────────
{trend_rows}Rate of change: {rate_fph:+.1f}°F/hour  ({direction})
Estimated time to reach target: {eta_str}
NOTE: rate of change reflects recent sensor history — if HVAC is currently
idle (see above) the trend may not continue without a setpoint adjustment.

{profile_header}
{profile_lines}
─── RECENT THERMOSTAT ACTIONS ─────────────────────────────────
{actions_str}
─── DECISION ───────────────────────────────────────────────────
Analyze the full picture. Consider whether to apply the formula's setpoint
now, modify it based on thermal trends and occupancy, or wait for the
environment to self-correct. The thermostat moves in 1°F steps — sub-degree
adjustments have no effect.

IMPORTANT: If HVAC is currently idle (see "HVAC currently" above), a "hold"
or "wait" decision means zones will drift toward ambient — only use hold/wait
when zones are already at or past target.  To resume heating/cooling, the
setpoint must cross the thermostat's current reading.

Return JSON only (no prose):
{{
  "action": "adjust" | "hold" | "wait",
  "setpoint_f": <integer 55–90, required only if action=adjust>,
  "wait_minutes": <integer 5–30, required only if action=wait>,
  "reasoning": "<2 sentences max>"
}}"""


# ---------------------------------------------------------------------------
# Safety protocol
# ---------------------------------------------------------------------------

class SafetyProtocol:
    """Last-resort safety gate — only blocks physically dangerous values."""

    @staticmethod
    def vet(
        decision: AdvisorDecision,
        desired_temp_c: float,
        max_offset_f: float,
        hvac_mode: str,
    ) -> AdvisorDecision:
        """Enforce hard physical limits on any advisor decision."""
        if decision.action not in ("adjust",):
            # hold / wait: no setpoint change, nothing to vet
            return decision

        sp = decision.setpoint_c
        orig_sp = sp
        changed = False

        # Rule 1: absolute physical bounds (55–90°F)
        if sp < _SAFETY_ABS_MIN_C:
            sp = _SAFETY_ABS_MIN_C
            changed = True
        elif sp > _SAFETY_ABS_MAX_C:
            sp = _SAFETY_ABS_MAX_C
            changed = True

        # Rule 2: max offset from schedule target
        max_offset_c = max_offset_f * 5.0 / 9.0
        low = desired_temp_c - max_offset_c
        high = desired_temp_c + max_offset_c
        if sp < low:
            sp = low
            changed = True
        elif sp > high:
            sp = high
            changed = True

        # Rule 3: wait cap
        if decision.action == "wait" and decision.wait_until:
            cap = datetime.now(UTC) + timedelta(minutes=_MAX_WAIT_MINUTES)
            if decision.wait_until > cap:
                decision = AdvisorDecision(
                    action=decision.action,
                    setpoint_c=decision.setpoint_c,
                    wait_until=cap,
                    reasoning=decision.reasoning,
                    from_llm=decision.from_llm,
                )
                logger.warning(
                    "SafetyProtocol: wait_until capped at %d minutes", _MAX_WAIT_MINUTES
                )

        if changed:
            logger.warning(
                "SafetyProtocol: setpoint %.1f°C (%.0f°F) clamped to %.1f°C (%.0f°F) "
                "— violated %s",
                orig_sp, _c_to_f(orig_sp),
                sp, _c_to_f(sp),
                "absolute bounds" if sp in (_SAFETY_ABS_MIN_C, _SAFETY_ABS_MAX_C) else "max-offset rule",
            )
            decision = AdvisorDecision(
                action=decision.action,
                setpoint_c=sp,
                wait_until=decision.wait_until,
                reasoning=decision.reasoning,
                from_llm=decision.from_llm,
            )

        return decision


# ---------------------------------------------------------------------------
# Main advisor class
# ---------------------------------------------------------------------------

class ClimateAdvisor:
    """LLM-driven thermostat decision advisor."""

    async def advise(
        self,
        *,
        db: Any,
        settings: Any,
        schedule_id: str,
        zone_sensor_ids: list[Any],
        zone_ids: list[Any],
        current_avg_c: float,
        desired_temp_c: float,
        formula_adjusted_c: float,
        hvac_mode: str,
        thermostat_c: float | None,
        current_setpoint_c: float,
        zone_names: str | None = None,
        thermal_profile: dict[str, Any] | None = None,
    ) -> AdvisorDecision:
        """Return a thermostat decision for the current maintenance tick.

        Falls back to the formula result (from_llm=False) on any error.
        """
        formula_decision = AdvisorDecision(
            action="adjust",
            setpoint_c=formula_adjusted_c,
            wait_until=None,
            reasoning="Formula-based offset compensation",
            from_llm=False,
        )

        # ── 1. AI toggle ────────────────────────────────────────────────────
        if not await self._is_enabled(db):
            logger.debug("ClimateAdvisor: AI advisor disabled — using formula result")
            return formula_decision

        # ── 2. Cache check ───────────────────────────────────────────────────
        cached = self._check_cache(schedule_id, current_avg_c)
        if cached:
            return cached

        # ── 3. Gather trend data + thermal profile ───────────────────────────
        trend = await _get_trend_data(db, zone_sensor_ids, zone_ids, desired_temp_c)
        profile = thermal_profile or {}

        # ── 4. Outdoor conditions (non-blocking) ─────────────────────────────
        outdoor_temp_c: float | None = None
        outdoor_condition = ""
        try:
            weather_entity = await self._get_weather_entity(db)
            if weather_entity and hasattr(settings, "_ha_client"):
                ha = settings._ha_client  # type: ignore[attr-defined]
                state = await ha.get_state(weather_entity)
                if state:
                    outdoor_temp_c = state.attributes.get("temperature")
                    outdoor_condition = state.state or ""
        except Exception:
            pass

        # ── 5. Time since last setpoint change ──────────────────────────────
        mins_since_change = 0
        if trend.last_actions:
            last_ts_str = trend.last_actions[0].get("at", "")
            try:
                last_ts = datetime.fromisoformat(last_ts_str)
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=UTC)
                mins_since_change = int((datetime.now(UTC) - last_ts).total_seconds() / 60)
            except (ValueError, TypeError):
                pass

        # ── 6. Retrieve temperature unit for prompt display ──────────────────
        temp_unit = getattr(settings, "temperature_unit", "F")

        # ── 7. Build prompt and call LLM ─────────────────────────────────────
        prompt = _build_prompt(
            hvac_mode=hvac_mode,
            desired_temp_c=desired_temp_c,
            avg_temp_c=current_avg_c,
            thermostat_c=thermostat_c,
            current_setpoint_c=current_setpoint_c,
            formula_adjusted_c=formula_adjusted_c,
            zone_names=zone_names,
            trend=trend,
            thermal_profile=profile,
            outdoor_temp_c=outdoor_temp_c,
            outdoor_condition=outdoor_condition,
            last_setpoint_changed_minutes=mins_since_change,
            temp_unit=temp_unit,
        )

        try:
            provider = await _build_llm_provider(db, settings)
            response = await asyncio.to_thread(
                provider.chat,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are the ClimateIQ smart thermostat advisor. "
                            "Analyze climate data and make predictive control decisions. "
                            "Return JSON only — no prose, no markdown."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=_LLM_MAX_TOKENS,
                temperature=_LLM_TEMPERATURE,
            )
            content = response["choices"][0]["message"]["content"].strip()
            decision = self._parse_response(content, formula_adjusted_c)
        except Exception as exc:
            logger.warning("ClimateAdvisor: LLM call failed (%s) — using formula", exc)
            return formula_decision

        # ── 8. Cache and return ──────────────────────────────────────────────
        _state[schedule_id] = _AdvisorState(
            decision=decision,
            decided_at=datetime.now(UTC),
            avg_c_at_decision=current_avg_c,
        )
        logger.info(
            "ClimateAdvisor [LLM]: action=%s setpoint=%.1f°C (%.0f°F) | %s",
            decision.action,
            decision.setpoint_c,
            _c_to_f(decision.setpoint_c),
            decision.reasoning,
        )
        return decision

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _is_enabled(self, db: Any) -> bool:
        """Read ai_advisor_enabled from system_settings (default: True)."""
        from sqlalchemy import select as sa_select

        from backend.models.database import SystemSetting

        try:
            result = await db.execute(
                sa_select(SystemSetting).where(SystemSetting.key == "ai_advisor_enabled")
            )
            row = result.scalar_one_or_none()
            if row and row.value is not None:
                val = row.value
                if isinstance(val, dict):
                    val = val.get("value", True)
                return bool(val)
        except Exception:
            pass
        return True  # enabled by default

    async def _get_weather_entity(self, db: Any) -> str | None:
        from sqlalchemy import select as sa_select

        from backend.models.database import SystemSetting

        try:
            result = await db.execute(
                sa_select(SystemSetting).where(SystemSetting.key == "weather_entity")
            )
            row = result.scalar_one_or_none()
            if row and row.value:
                val = row.value
                if isinstance(val, dict):
                    val = val.get("value", "")
                return str(val) if val else None
        except Exception:
            pass
        return None

    def _check_cache(self, schedule_id: str, current_avg_c: float) -> AdvisorDecision | None:
        """Return cached decision if still valid, else None."""
        cached = _state.get(schedule_id)
        if not cached:
            return None

        now = datetime.now(UTC)

        # Respect "wait" decisions until the wait expires
        if (
            cached.decision.action == "wait"
            and cached.decision.wait_until
            and now < cached.decision.wait_until
        ):
            logger.debug(
                "ClimateAdvisor: respecting wait until %s", cached.decision.wait_until
            )
            return cached.decision

        # Invalidate if zone avg has moved significantly (≥1°F) or cache is stale
        avg_delta = abs(current_avg_c - cached.avg_c_at_decision)
        age_minutes = (now - cached.decided_at).total_seconds() / 60
        if avg_delta >= _LLM_CACHE_TEMP_DELTA_C or age_minutes >= _LLM_CACHE_MINUTES:
            return None

        logger.debug(
            "ClimateAdvisor: cache hit (age=%.1f min, avg_delta=%.2f°C)",
            age_minutes, avg_delta,
        )
        return cached.decision

    def _parse_response(
        self, content: str, formula_adjusted_c: float
    ) -> AdvisorDecision:
        """Parse LLM JSON response into an AdvisorDecision."""
        # Strip any markdown fences the model might add despite instructions
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(l for l in lines if not l.startswith("```"))

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON object from surrounding text
            import re
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                data = json.loads(m.group())
            else:
                raise ValueError(f"No JSON found in LLM response: {text[:200]!r}")

        action = str(data.get("action", "adjust")).lower()
        if action not in ("adjust", "hold", "wait"):
            action = "adjust"

        reasoning = str(data.get("reasoning", "")).strip()[:300]

        if action == "wait":
            wait_minutes = int(data.get("wait_minutes", 10))
            wait_minutes = max(5, min(wait_minutes, _MAX_WAIT_MINUTES))
            return AdvisorDecision(
                action="wait",
                setpoint_c=formula_adjusted_c,
                wait_until=datetime.now(UTC) + timedelta(minutes=wait_minutes),
                reasoning=reasoning or f"LLM: wait {wait_minutes} minutes",
                from_llm=True,
            )

        if action == "hold":
            return AdvisorDecision(
                action="hold",
                setpoint_c=formula_adjusted_c,
                wait_until=None,
                reasoning=reasoning or "LLM: hold current setpoint",
                from_llm=True,
            )

        # action == "adjust"
        setpoint_f = float(data.get("setpoint_f", _c_to_f(formula_adjusted_c)))
        setpoint_c = (setpoint_f - 32.0) * 5.0 / 9.0
        return AdvisorDecision(
            action="adjust",
            setpoint_c=setpoint_c,
            wait_until=None,
            reasoning=reasoning or f"LLM: set to {setpoint_f:.0f}°F",
            from_llm=True,
        )
