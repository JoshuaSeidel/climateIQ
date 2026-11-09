"""Manual thermostat control shared by every user-facing entry point.

Dashboard "Set", the zone "Set" button, the dial, chat ``set_override`` and
``/chat/command``, and the boost/eco quick actions all go through
``apply_manual_setpoint`` (spec §2), so they behave identically:

* the value is in the user's *display* unit (``display_unit``), validated
  against the safety range and **rejected** — never clamped — when outside
  it (spec §5.6);
* heat or cool is chosen from the rooms (zone average vs the requested
  temperature), respecting the heat/cool lock, seasonal lock and time
  windows — an override that leaves the thermostat in cool while the user
  asks for warmth would silently do nothing, now that the override pauses
  automatic control (spec §4);
* the write uses Home Assistant's unit and the matching side of a dual
  setpoint (spec §1, §5.7);
* the override is recorded so automatic control pauses until it expires.

``resume_automatic_control`` is the one implementation of Resume /
cancel-override.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class ManualControlError(ValueError):
    """The request can't be applied (bad value, no thermostat, …)."""


async def display_unit(db: Any) -> str:
    """The user's display unit ("F"/"C"): the setting, else the add-on option.

    One implementation for GET and POST so a value read from the API can be
    sent straight back (previously GET defaulted to C and POST to F).
    """
    from sqlalchemy import select

    from backend.config import SETTINGS
    from backend.models.database import SystemSetting

    row = (
        await db.execute(select(SystemSetting).where(SystemSetting.key == "temperature_unit"))
    ).scalar_one_or_none()
    raw = (row.value or {}).get("value") if row is not None and row.value else None
    unit = str(raw or SETTINGS.temperature_unit or "C").strip().upper()
    return "F" if unit == "F" else "C"


async def climate_entity(db: Any) -> str:
    from sqlalchemy import select

    from backend.config import SETTINGS
    from backend.models.database import SystemSetting

    row = (
        await db.execute(select(SystemSetting).where(SystemSetting.key == "climate_entities"))
    ).scalar_one_or_none()
    raw = str((row.value or {}).get("value", "") if row is not None and row.value else "")
    entity = raw.split(",")[0].strip() or (SETTINGS.climate_entities or "").split(",")[0].strip()
    if not entity:
        raise ManualControlError("No climate entity configured")
    return entity


async def is_managed_thermostat(db: Any, entity_id: str | None) -> bool:
    """Is this the whole-house thermostat the control loops own?"""
    if not entity_id:
        return False
    try:
        return entity_id.strip() == await climate_entity(db)
    except ManualControlError:
        return False


def in_safety_range(temp_c: float) -> bool:
    from backend.config import SETTINGS

    return SETTINGS.safety_min_temp_c <= temp_c <= SETTINGS.safety_max_temp_c


def to_celsius(value: float, unit: str) -> float:
    return (value - 32.0) * 5.0 / 9.0 if unit == "F" else value


async def apply_manual_setpoint(
    db: Any,
    display_temp: float,
    *,
    source: str,
    duration_hours: float | None = None,
    force_mode: str | None = None,
) -> dict[str, Any]:
    """Validate, choose direction, write, and record a manual override."""
    import backend.api.dependencies as deps
    from backend.config import SETTINGS
    from backend.core.manual_override import set_override

    ha = deps._ha_client
    if ha is None:
        raise ManualControlError("Home Assistant client not connected")
    try:
        value = float(display_temp)
    except (TypeError, ValueError) as exc:
        raise ManualControlError("temperature must be a number") from exc

    unit = await display_unit(db)
    requested_c = to_celsius(value, unit)
    lo, hi = SETTINGS.safety_min_temp_c, SETTINGS.safety_max_temp_c
    if not lo <= requested_c <= hi:
        lo_d, hi_d = (lo * 9 / 5 + 32, hi * 9 / 5 + 32) if unit == "F" else (lo, hi)
        raise ManualControlError(
            f"temperature {value:g}°{unit} is outside the safety range "
            f"{lo_d:.0f}-{hi_d:.0f}°{unit}"
        )
    entity = await climate_entity(db)
    if duration_hours is not None:
        import math

        try:
            duration_hours = float(duration_hours)
        except (TypeError, ValueError) as exc:
            raise ManualControlError("duration_hours must be a number") from exc
        if not math.isfinite(duration_hours) or duration_hours <= 0:
            raise ManualControlError("duration_hours must be a positive number")
        duration_hours = min(duration_hours, 24.0)  # spec §2: max 24 h

    # Everything below runs under the thermostat lock: the override is
    # recorded *before* any write (so no loop tick can slip in between and
    # undo it), and rolled back if the write fails.
    from backend.core.control_lock import THERMOSTAT_LOCK
    from backend.core.manual_override import restore_override, snapshot_override

    async with THERMOSTAT_LOCK:
        previous = await snapshot_override(db)
        try:
            _before = await ha.get_state(entity)
            mode_before = str(_before.state or "").lower() if _before else ""
        except Exception:
            mode_before = ""
        expires = await set_override(
            db, requested_c, source=source, duration_hours=duration_hours
        )
        try:
            actual = await _write_manual(db, ha, entity, requested_c, force_mode)
        except BaseException:
            # A rejected/failed request changes nothing: the override that
            # was running (if any) stays — and so does the heat/cool mode
            # (a switch made before the setpoint write failed would leave
            # the restored override unable to work).
            if mode_before:
                try:
                    _now = await ha.get_state(entity)
                    if _now is not None and str(_now.state or "").lower() != mode_before:
                        await ha.set_hvac_mode(entity, mode_before)
                        import backend.api.main as main_mod

                        await main_mod._record_mode_switch(entity, mode_before)
                except Exception as exc:
                    logger.warning("Could not restore the thermostat mode: %s", exc)
            await restore_override(db, previous, reason="manual write failed")
            raise
    return await _finish_manual(db, entity, value, unit, requested_c, actual, source, expires)


async def _write_manual(
    db: Any, ha: Any, entity: str, requested_c: float, force_mode: str | None
) -> str:
    """Choose direction and write the setpoint (caller holds the lock)."""
    import backend.api.main as main_mod
    from backend.core.temp_compensation import get_avg_zone_temp_c

    # ── Direction (spec §4) ──────────────────────────────────────────────
    mode = force_mode
    if mode is None:
        picked, _urgent = await main_mod._auto_select_hvac_mode(
            ha, entity, requested_c, db=db, zone_ids=None
        )
        mode = picked
    if mode is None:
        # Inside the dead-band the selector keeps the current mode — but an
        # off/auto thermostat would then never hold the override (and the
        # override pauses every loop).  Pick by the sign of the error.
        cur = await ha.get_state(entity)
        if str(cur.state or "").lower() not in ("heat", "cool"):
            avg_c, _ = await get_avg_zone_temp_c(db, None, ha)
            if avg_c is not None:
                mode = "heat" if requested_c >= avg_c else "cool"
    if mode in ("heat", "cool"):
        allowed = await main_mod._direction_allowed(db, ha, mode, None, None)
        if not allowed:
            raise ManualControlError(f"{mode} is not allowed right now (lock or time window)")
        state = await ha.get_state(entity)
        if str(state.state or "").lower() != mode:
            await ha.set_hvac_mode(entity, mode)
            await main_mod._record_mode_switch(entity, mode)
    state = await ha.get_state(entity)
    actual = str(state.state or "").lower()
    if actual in ("heat", "cool"):
        intent: str | None = actual
    else:
        avg_c, _ = await get_avg_zone_temp_c(db, None, ha)
        intent = "cool" if avg_c is not None and avg_c > requested_c else "heat"

    # ── Write in HA's unit (spec §1) ─────────────────────────────────────
    is_f = await main_mod._ha_unit_is_f(ha)
    value_ha = round(requested_c * 9 / 5 + 32, 1) if is_f else round(requested_c, 1)
    try:
        await ha.set_temperature_with_hold(entity, value_ha, intent_mode=intent)
    except Exception:
        await ha.set_temperature(entity, value_ha, intent_mode=intent)
    return actual


async def _finish_manual(
    db: Any,
    entity: str,
    value: float,
    unit: str,
    requested_c: float,
    actual: str,
    source: str,
    expires: datetime,
) -> dict[str, Any]:
    # Audit (best effort).
    try:
        from sqlalchemy import select

        from backend.models.database import Device, DeviceAction
        from backend.models.enums import ActionType, TriggerType

        dev = (
            await db.execute(select(Device).where(Device.ha_entity_id == entity).limit(1))
        ).scalar_one_or_none()
        if dev is not None:
            db.add(DeviceAction(
                device_id=dev.id,
                triggered_by=TriggerType.user_override,
                action_type=ActionType.set_temperature,
                parameters={"temperature": value, "unit": unit, "source": source},
                reasoning=f"Manual override ({source})",
            ))
            await db.commit()
    except Exception as exc:
        logger.debug("Manual override audit failed: %s", exc)

    logger.info(
        "Manual override from %s: %.1f°%s (%s) until %s", source, value, unit, actual, expires
    )
    return {
        "temperature": value,
        "unit": unit,
        "temperature_c": requested_c,
        "mode": actual,
        "override_until": expires.isoformat(),
    }


async def resume_automatic_control(db: Any, *, source: str) -> str:
    """Resume / cancel override — one implementation (spec §2).

    Clears the override, then hands the thermostat back to whoever owns it:
    in Learn that's the Ecobee's own program; otherwise the owning loop is
    run now (in the background for Active mode, whose tick may call the LLM).
    """
    import asyncio

    from sqlalchemy import select

    import backend.api.dependencies as deps
    import backend.api.main as main_mod
    from backend.core.control_lock import THERMOSTAT_LOCK
    from backend.core.manual_override import clear_override
    from backend.models.database import SystemConfig
    from backend.models.enums import SystemMode

    # Under the lock so a concurrently failing manual request can't restore
    # the override after Resume cleared it.
    async with THERMOSTAT_LOCK:
        await clear_override(db, reason=source)
    cfg = (await db.execute(select(SystemConfig).limit(1))).scalar_one_or_none()
    mode = cfg.current_mode if cfg is not None else SystemMode.learn
    if mode == SystemMode.learn:
        ha = deps._ha_client
        if ha is not None:
            try:
                await ha.resume_ecobee_program(await climate_entity(db), resume_all=True)
            except Exception as exc:
                logger.debug("resume_ecobee_program failed: %s", exc)
        return "Returned control to the thermostat's own program (Learn mode)."
    if mode == SystemMode.active:
        task = asyncio.create_task(main_mod.execute_active_mode())
        main_mod._background_tasks.add(task)
        task.add_done_callback(main_mod._background_tasks.discard)
        return "Resumed AI control."
    await main_mod.execute_schedules()
    await main_mod.maintain_climate_offset()
    if mode == SystemMode.follow_me:
        await main_mod.execute_follow_me_mode()
    return "Resumed schedule control."
