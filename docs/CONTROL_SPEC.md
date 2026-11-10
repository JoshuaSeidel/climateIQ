# ClimateIQ heating & cooling — control specification

This is the single source of truth for how ClimateIQ decides **heat vs cool**
and **what setpoint** the thermostat gets.  Code, tests
(`backend/tests/scenario/`) and reviews are checked against it.  Rules marked
**[Decision]** settle questions the code previously answered inconsistently.

Setup assumed: one whole-house thermostat (Ecobee via Home Assistant), many
rooms ("zones") with their own temperature sensors.  Heating or cooling for
one room affects every room.

---

## 1. Units

1. Internally every temperature is °C.  Values are converted at the edges:
   to/from Home Assistant's unit system (thermostat setpoints/readings), to/from
   the user's display unit (API, UI, LLM prompts), and from each HA weather
   entity's own `temperature_unit` (outdoor temps).
2. Temperature *differences* convert with ×9/5 only — never +32.

## 2. Who may write to the thermostat

Exactly one owner per situation.  Every other path must not write.

| System mode | Owner of setpoint + heat/cool mode |
|---|---|
| **Learn** | Nobody.  Control is handed back to the Ecobee's own program (resume program, restore its occupancy features; only if ClimateIQ itself last switched it `off`, set its own `heat_cool`/`auto` — an `off` the user chose stays off).  No schedule firing, preconditioning, maintenance, drift correction, AI, or Follow-Me writes. |
| **Scheduled** | Schedule control: `maintain_climate_offset` (every minute) for whichever schedule governs (§3, §7).  **[Decision]** In a gap (nothing governs) the Ecobee's own program owns the thermostat: ClimateIQ hands back once (resume program) when the last schedule ends, after Resume, or when an override expires in a gap.  `execute_schedules` never writes — it only sends "Schedule Activated" and decides preconditioning.  Drift correction only re-runs maintenance. |
| **Follow-Me** | If a schedule is active → schedule control exactly as in Scheduled mode.  Only when **no** schedule is active does the Follow-Me loop control by occupancy. **[Decision]** Schedules are binding in every automatic mode. |
| **Active (AI)** | `execute_active_mode`, bounded by the schedule guard (§6).  The final guarded write runs every tick, even when the LLM is skipped or unavailable (formula fallback). |

Overrides on top of any automatic mode:

- **Manual override** (Dashboard "Set", zone "Set", the dial, chat
  override/commands, boost/eco/away quick actions): **[Decision]** one
  implementation (`core/manual_control.py`).  The value is in the display
  unit and is **rejected** outside the safety range; heat or cool is chosen
  from the rooms (respecting locks/windows) so the override actually works;
  it pauses *all* automatic setpoint and heat/cool writes until it expires:
  the **next schedule boundary** (any enabled schedule starting or ending),
  `duration_hours` if given (max 24 h), or **Resume**.  Resume, switching
  system mode, and emergency shutoff clear it immediately.  If the Ecobee
  drops the hold itself, the override setpoint is re-asserted.
- A rejected or failed manual request changes nothing — an override
  already running stays.  If the thermostat is `off`/`auto`, the override
  picks heat or cool by the sign of (requested − rooms) so it actually
  holds.  Chat's zone tool and commands use the same path (sensor-only
  zones included, `hold_minutes` honoured).
- **Resume** (quick action, dial long-press, chat cancel): clears the
  override and hands control to its owner — the Ecobee program in Learn,
  otherwise the owning loop runs immediately.
- **Boost heat/cool**: 2 °F past the current setpoint in that direction
  (switching to it).  **Eco**: the eco preset if available, else 3 °F in the
  energy-saving direction of the current mode.
- "Override" in the dashboard/dial/API means **only** this ClimateIQ
  override (`is_override`, `override_temp`, `override_until`) — never an
  Ecobee hold, which ClimateIQ itself creates on every write.
- **Every automatic write — setpoint and heat/cool switch —
  re-checks** that its loop still owns the thermostat and the override right
  before writing, under the single thermostat lock that manual writes, mode
  changes and emergency shutoff also take, so an emergency shutoff / Learn /
  override issued during a long tick (LLM call) always wins.
- **Emergency shutoff** turns the thermostat off and switches to Learn (and
  hands the Ecobee its program and occupancy features, but leaves it `off`).
  The off is the *user's*: every "ClimateIQ turned it off" record and any
  pending Learn restore (§9.1d) is forgotten whether or not the HA off
  command succeeds.  A database problem in the switch-to-Learn / clear-override
  steps is reported but never stops the off command, and the record cleanup
  runs after the HA commands and is fully guarded — nothing can prevent the
  shutoff itself.
- The **rule engine** never writes to the managed thermostat or sends
  setpoints to non-climate devices.
- Changes made directly on the physical thermostat in an automatic mode are
  treated as drift and corrected **[Decision — unchanged behaviour]**; use
  ClimateIQ (dashboard/dial) to override.

## 3. Which schedule is active

1. A schedule is active at local time *t* if it is enabled, *t* falls inside
   `[start, end)` (end-exclusive, so back-to-back schedules never overlap),
   and the **start day** is one of its days.  An end at or before the start
   wraps past midnight (end == start = 24 h); the portion after midnight
   belongs to the day it **started** **[Decision]** (a Friday 20:00-06:30
   schedule is active Saturday 03:00 even if Saturday isn't ticked).  No end
   time = until midnight.  Conflict detection uses the same timeline, and
   flags overlaps even when the schedules cover different zones (there is
   one thermostat, so only one can govern).
2. If several are active, the highest `priority` wins; ties go to the one that
   started most recently.
3. Local time comes from `backend/core/user_tz.py` everywhere.
4. One implementation (`schedule_guard.find_active_schedule`, and
   `governing_schedule` which adds §7 preconditioning) is used by every loop
   **and** the API (dashboard "Set:", `/schedules/active`, Resume).  Known
   drift: `/schedules/active` and the offset-debug endpoint use
   `find_active_schedule` and so ignore preconditioning, while the status
   endpoint's "Set:" (`schedule_target_temp`) uses `governing_schedule`; during
   the warm-up window before a schedule the zone cards and Active Schedule
   card show no target while "Set:" shows the upcoming one.  The status
   endpoint's `schedule_target_temp` is empty (null) while an `off` schedule
   governs — there is no target to show.
5. Local time falls back to the last timezone read from Home Assistant if a
   lookup fails, never silently to UTC.
6. DST: a start/end inside the spring-forward gap (e.g. 02:30) happens at the
   transition (03:00); in the repeated fall-back hour the first pass counts.

## 4. Heat or cool

1. Decided from the governing zones' **sensor average** vs the governing target
   — never from the thermostat's own sensor.  Governing zones = the active
   schedule's zones (empty list = all non-excluded zones).
2. Switch to heat when zones are more than the dead-band (0.6 °C ≈ 1 °F) below
   target; to cool when more than the dead-band above.  Inside the dead-band,
   keep the current mode — except `off` (after an off schedule): leave it once
   rooms are past the §5.4 run threshold.
3. Never select the thermostat's own `auto`/`heat_cool`; move off it.
4. Restrictions, in order, can only *forbid* a direction:
   a schedule set to `off` → off;
   `hvac_control_mode` heat/cool lock;
   seasonal lock (with its outdoor escape valves);
   time-of-day windows (with their escape valves; an active window takes
   precedence over the season).
   If the needed direction is forbidden, leave the mode alone and park the
   setpoint so the forbidden direction can't run (§5.5).  **[Decision]** A
   lock never *picks* a direction: a heat lock does not switch a too-warm
   house to heat; it only stops cool.
5. Reversal cooldown (default 30 min) between heat↔cool switches, bypassed
   when the thermostat is working *against* the target (heating while zones are
   above target or cooling while below).
6. The LLM advisor may *suggest* a direction; the suggestion is dropped if it
   contradicts any rule above or the sign of the zone error.

## 5. Setpoint

1. **Goal:** bring the governing zones to the governing schedule's target.
2. **Offset compensation:** the thermostat measures its own location, so the
   setpoint is shifted by the zone error (whole °F steps, max `max_temp_offset_f`).
3. **Never drive into rooms already past target.**  Heat mode with zones at or
   above target → setpoint ≤ min(target, thermostat reading) so heat stays off.
   Cool mode with zones at or below target → setpoint ≥ max(target, thermostat
   reading).  The dead-band (zones within 1 °F on the self-correcting side)
   follows the same rule.
4. **Must actually run when needed:** heat with zones more than 0.28 °C
   (≈ ½ °F) below target → setpoint strictly above the thermostat's reading
   (and ≥ target); cool mirror.  This threshold is smaller than the 1 °F
   direction dead-band, so a room 1 °F short in heat mode still gets heat.
   The setpoint never exceeds target ± max offset (§5.2), even if the room
   never warms (closed vent, stuck sensor).  Hysteresis: once it had to run,
   that setpoint may stand until the rooms reach target (no on/off cycling
   when a room hovers at the threshold).
5. **Time-window / lock block:** if the current mode's direction is forbidden,
   park the setpoint 1 °F past the thermostat reading (heat: below; cool:
   above), or at the safety limit if there is no reading.  This is applied
   before any "already at target, skip write" shortcut.  On a dual-setpoint
   thermostat the forbidden side is parked 3 °F past the reading once it
   comes within 1 °F of it; a side already parked is not rewritten.
6. **Safety range:** every write is clamped to `safety_min_temp_c ..
   safety_max_temp_c`; implausible values are rejected, not clamped into range.
7. **Dual-setpoint thermostats** (heat_cool): writes move the side matching the
   direction (heat → `target_temp_low`, cool → `target_temp_high`); reads use
   the same side.
8. **LLM advisor** output always passes SafetyProtocol: absolute bounds, max
   offset, rules 5.3/5.4, and a "wait" while zones are far off target is
   converted into an adjustment.

## 6. Active (AI) mode

1. The governing schedule binds (§3).  The AI may be **more** aggressive toward
   its target than Schedule mode would be, never less:
   zones too warm → setpoint ≤ Schedule-mode setpoint; too cold → ≥; on target
   → within 0.5 °C of it.
2. With no active schedule, the AI targets the zones' comfort-band midpoints
   (§5 applies to that target).  Only zones with their own band govern;
   zones without one count only when no zone has a band (then at the
   default comfort range).  A cold unbanded basement must not drive heat
   into rooms already inside their band.
2a. The LLM prompt treats a *next* schedule as information only.  The
    prompt is intentionally in °F (whole-°F thermostat); conversion to °C
    happens on the way back, before the guard.
2b. If the schedule's own zones are all excluded from metrics, other rooms
    never stand in for them — no write that tick.  LLM failures fall back to
    the offset formula; the guarded write still happens.
3. Heat/cool selection follows §4 every tick.
4. Fans: only zones with `allow_fan_control`; never below the user's baseline;
   quiet-hours cap (default 50 % 19:00–07:00) — on a stepped fan the
   highest speed at or below the cap (3-speed: low); the value the fan
   actually reports is tracked, so its own speed snapping is never mistaken
   for a hand change and a capped fan isn't rewritten each tick; fans
   changed by hand are left alone; raised fans are released when the governing zones reach target;
   override state survives restarts.

## 7. Preconditioning (starting early)

1. Only in a **gap** — when no other schedule is active.  An active schedule
   always wins until it ends.  Never for a schedule set to `off`.
2. Only when the thermostat's current direction moves the upcoming schedule's
   zones toward its target by more than the dead-band (warm-up in heat mode,
   cool-down in cool mode); never by switching the HVAC off early.
3. Lead time from the learned rate, floored at 1 °C/h, max 3 h, measured in
   real elapsed time (DST-safe); upcoming starts are found past midnight.
   Zones = the schedule's zones, or all zones if none are listed.
4. Once started, the upcoming schedule governs maintenance / AI until it
   starts, unless another schedule becomes active first.

## 6b. Follow-Me

1. Only in gaps (§2).  Occupancy from the shared inference
   (`infer_zone_occupancy`); a presence sensor's last state holds until it
   changes (up to 24 h); a zone with no fresh signal keeps its last stable
   state; if no zone has occupancy data, no change.
2. Occupied zones → their comfort-band midpoints.  Nobody home → a 2 °C
   setback from the house midpoint in the energy-saving direction of the
   current mode (no fixed 18 °C).  The setback **never switches heat↔cool**
   — an empty house is not a reason to start the other direction.
3. Occupancy changes only after a dwell (3 min to become occupied, 10 min
   to become vacant), so a single motion blip doesn't move the target.

## 8. Notifications

At most one per meaningful change: schedule start, heat↔cool switch, a
setpoint move of ≥ 1 °C (and ≥ 1 h since the last), sensor offline (once per
episode).  A tick that switches heat↔cool sends only the switch
notification.  Temperatures are shown in the user's display unit
(Settings).  "Already notified" and preconditioning state survive restarts.

## 8b. Escape valves

Time-window and seasonal-lock escape valves have hysteresis: once open they
stay open until the reading recovers 1 °F (windows) / 1 °C (outdoor) past the
threshold.

## 9. Robustness

1. A failure reading one sensor/entity never aborts the whole tick.
   If HA is unreachable when the add-on starts, control resumes on its own
   once HA is up (the HA client connects on demand; no restart needed).
   Thermostat events always reach the drift/override handler, even right
   after the thermostat entity is changed in Settings.
1a. Seasonal "excluded months" use the local month, not UTC's.  A missing
   outdoor reading leaves the seasonal escape valve as it was (latched),
   and a season saved as ending Feb 28 also covers Feb 29.
1b. The last heat/cool/off switch survives restarts (reversal cooldown,
   Learn's "did ClimateIQ turn it off" check).
1c. The rule engine's humidity actions only ever turn on a humidifier or
   dehumidifier — never a fallback device.
1d. **Handing back an `off` ClimateIQ set** (Scheduled gap handback, Learn
   handover) is *one-shot for the Ecobee resume* and *bounded for the
   restore*: `resume_program` is sent once and never repeated by a retry
   (a repeat would wipe a hold the user placed meanwhile).  If restoring the
   Ecobee's own `heat_cool`/`auto` fails (HA error, or the thermostat is
   `unavailable`/`unknown`), only that restore is retried, at most 5 ticks
   (gap: in-memory counter; Learn: a stored one-shot marker consumed by the
   next tick in any mode and acted on only in Learn), then an error is
   logged and the "ClimateIQ turned it off" record is cleared (Learn,
   gap) so a later off the user chooses is never mistaken for ClimateIQ's.
   Known limitations: the retry can set the Ecobee's own mode over an off the
   user chose within those few ticks; a restart during it loses the
   counter.
1e. The "ClimateIQ turned it off" record is dropped when a tick sees the
   thermostat in a real mode other than `off` more than 15 s after the record
   was written (HA reports state asynchronously, so a read in the same tick
   as ClimateIQ's own off can still show the old mode).  The final guarded
   write may hand ClimateIQ's own `off` back to the Ecobee's program when no
   sensor data exists and no `off` schedule governs (`restore_off`, opt-in
   per caller — an off schedule must never undo its own off).
1f. A thermostat reporting `unavailable`/`unknown` is not mode-switched and
   not notified about every tick (nothing can be written to it).
1g. Input validation: stored data is never re-validated on read (zone
   responses use validator-free models); settings such as
   `max_temp_offset_f` accept 0 (offset compensation disabled) up to 20 °F,
   zone `comfort_preferences` temps are Celsius (-10..50, min ≤ max),
   `climate_entities` are `climate.*` ids.
2. Missing data → do nothing rather than guess (no zone readings → no
   direction change; no thermostat reading → no compensation).
3. Every loop is idempotent: running it twice in a row with unchanged inputs
   makes at most one write.  The final guard (`core/write_guard.py`) returns
   an allowed *band*; a setpoint already inside it is not rewritten.
4. The heat/cool cooldown applies only to heat↔cool reversals; it is skipped
   only when the thermostat is *running* (hvac_action) against the target.
