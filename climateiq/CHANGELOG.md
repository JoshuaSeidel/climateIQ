# Changelog

## [1.0.60] - 2026-07-04

### Changed
- **HA notification throttle: 30-min floor + change-only.** Active-mode and Follow-me were firing an HA push every 5-minute tick whenever the setpoint crossed the 0.5°C dead-band, even when the new value was identical to what we'd just sent.  `NotificationService.send_ha_notification` now keeps a per-(title, target) memo of the last successfully-sent message and its timestamp; a new send is emitted only when **both** (a) the message text differs from the last one **and** (b) at least 30 minutes have elapsed since it.  Result: the "AI Mode → 72°F …" and "Follow-Me Mode → …" notifications fire on real state changes, capped at ~2/hour per key, instead of every tick.
- Schedule and sensor-offline notifications are unaffected in practice — they already dedupe upstream and their titles vary per schedule / sensor, so they miss the throttle key.  Callers can pass `bypass_throttle=True` for one-shot events that shouldn't be filtered.

## [1.0.59] - 2026-07-04

### Added
- **Searchable HA fan-entity picker in the zone editor.** The Fan Entities card now hits `/settings/ha/entities?domain=fan` and shows a dropdown of your Home Assistant fans as you type — the same UX as the sensor form's HA Entity picker. Click to attach, or type/paste a raw entity_id and press Enter for anything not auto-discovered. Already-attached fans are filtered out of the dropdown so you can't add the same fan twice.

## [1.0.58] - 2026-07-04

### Added
- **Floor-aware Active-mode prompt.** Focus and constraint zones are now grouped by `floor` in the LLM prompt (e.g. `Floor 2:`, `Floor 1:`, `Basement`, `(unset)`), and a new `Occupied floors: …` line surfaces the set of floors that currently have stable-occupied zones. System-prompt rule (6) tells the model to bias the setpoint toward the occupied floor(s) rather than averaging the whole house. Single-floor houses collapse gracefully to the pre-1.0.58 flat listing.
- **Per-zone system fan control (opt-in, baseline-preserving).** New `Zone.allow_fan_control` boolean column (auto-migrated) and a toggle inside the Zones editor's Fan Entities card. When enabled, Active mode may ramp those `fan.*` entities 0–100 % via a new `FAN_ACTIONS:` line in the LLM output. Guardrails:
  - Excluded zones (`exclude_from_metrics=True`) always deny — enforced both in the UI (checkbox disabled) and in the backend (`zone_allow_fan_control` gate).
  - **Baseline preservation.** The first time we touch a fan we capture the user's pre-existing percentage as a baseline. We never write below that baseline; if the LLM asks for a lower value we no-op. On "release" (target ≤ baseline while we hold override), we restore *exactly* to the baseline instead of turning the fan off, so a fan the user had running at 50 % goes back to 50 %, not off. `fan.turn_off` is only used when the captured baseline was 0.
  - Per-fan write cooldown of 5 min prevents flapping, and stale baselines (30 min of no LLM interaction) are dropped so we don't clobber a fresh user setting.
- **`FAN_ACTIONS` LLM output line.** Response format grew from two lines to three: `RECOMMENDED_TEMP` / `FAN_ACTIONS: <zone>=<pct>|<zone>=<pct>` (omit if none) / `REASON`. System-prompt rule (7) tells the model to nudge fans on rooms that need extra help and to leave `fan_ctrl_ok` unmarked zones alone.

### Changed
- **Active-mode `zone_data` payload** now carries `floor`, `fan_entities`, and `allow_fan_control` for every focus and constraint zone, and the compact `[…]` extras tag now includes `fan_ctrl_ok` on eligible zones.

## [1.0.57] - 2026-07-04

### Added
- **Occupancy dwell hysteresis.** Raw multi-signal occupancy is now stabilized before it can drive an HVAC decision: 3 min of continuous OCC before flipping to *stable occupied*, 10 min of continuous VAC before flipping to *stable vacant*. Cuts "someone walked past the door" and "lights just turned on" churn. The Active-mode LLM only sees the stable value; the change-gate hash uses the stable value; anti-oscillation and steady-state gates all benefit.
- **Trend-aware Active mode.** `OccupancyPattern.schedule` is now consulted per focus zone for the current 5-min slot AND the next 30 min. If a currently-vacant focus zone has ≥60% probability of being occupied within the next half-hour, the prompt tags it `arriving_soon p<N>` — the LLM is instructed to precondition as if it were already occupied. The learning loop that populates OccupancyPattern already runs; this makes it actionable.
- **Weather-aware heavy-day preconditioning.** Active-mode now pulls the 12-hour forecast from Redis (`weather:forecast`), computes the max high and min low, and tags the prompt `HEAVY_HOT_DAY (precondition cool now)` or `HEAVY_COLD_DAY (precondition heat now)` when the day will hit ≥32°C or ≤2°C. The LLM system prompt calls out heavy-day rule (4) explicitly.
- **HVAC cycle-prevention — setpoint anti-oscillation.** On top of the existing 30-min mode-switch cooldown, each thermostat now has a 15-min *direction lockout*: if we just moved the setpoint up, we won't move it down again for 15 minutes (and vice-versa). Blocks the "raise 1°, drop 1° four ticks later" pattern that beats up compressors even inside a single mode.
- **Fan awareness.** New `Zone.fan_entities` JSON column (auto-migrated) + zone-editor picker. When any of a zone's `fan.*` entities is running, the prompt tags the zone `fan_on` and system-prompt rule (3) tells the LLM to tolerate ~1.5°F beyond the comfort band because airflow makes a room feel cooler than it reads.
- **Energy + solar awareness.** Three new settings entities — `solar_production_entity`, `grid_export_entity`, `battery_soc_entity` — join the existing `energy_entity`. Active-mode polls all four in parallel each tick and appends a compact `Energy: solar Xkw, house Ykw, exporting Zkw, battery N%, SURPLUS (favor comfort)` line to the LLM prompt when data is available. System-prompt rule (5) tells the model to favor comfort under solar surplus and nudge conservatively otherwise. Settings page grew three new HA-entity selectors.

### Changed
- **All Active-mode LLM I/O is now in the user's display unit.** For `F`-unit users the prompt, comfort bands, thermostat readouts, safety range, and the `RECOMMENDED_TEMP:` response are all in °F — no more "notification says 22°C" surprises. Internally we still store and clamp in Celsius; the conversion happens at the LLM boundary via `_c_to_disp`/`_disp_to_c` helpers, plus the response parse round-trips through Celsius so safety and constraint-guard clamps stay stable.
- **Active-mode LLM system prompt now has five explicit rules** — hold-if-in-band, arriving-soon precondition, fan-widened band, heavy-day precondition, and energy-surplus/deficit bias — instead of the earlier single instruction. Still two lines out; still frugal on tokens.

## [1.0.56] - 2026-07-04

### Added
- **Focused Active mode.** The AI now treats the currently-active schedule's zones as *focus zones* (hit the target) and every other zone as a *constraint zone* (must stay inside its comfort band). The LLM prompt is restructured accordingly, and a soft "constraint guard" clamps the LLM's setpoint recommendation up or down when it would push a constraint zone further out of its comfort band than it already is.
- **Open-ended per-zone HA occupancy entities.** New `Zone.ha_entities` JSON column (auto-migrated) lets you attach any HA entities you want — motion sensors, door/window contacts, light and switch states, plugs, media players, etc. — to a zone. `infer_zone_occupancy` now polls those entities in parallel and treats any recent motion or any "on" state as a dominant occupancy signal (weight 0.7). Curated user signals now outrank the flaky multisensor motion channel. New Occupancy Signals card in the zone editor to manage the list.

### Changed
- **Active-mode LLM cost dropped ~60-70%.** Two new skip gates run before every LLM call: (1) *steady-state skip* — if focus zones are within ±0.5°C of the schedule target and every constraint zone is inside its comfort band, no call; (2) *change-gate hash + 10 min floor* — if a hash of (rounded focus temps, constraint-in-band bits, hvac mode, current setpoint, outdoor °C band, active schedule id) matches the previous real call AND fewer than 10 minutes have elapsed, no call. The prompt itself is now compact (single-line system directive, no humidity/lux, unoccupied constraint zones stripped) — roughly a 2/3 reduction in tokens per call. The 5-minute tick still fires, but most ticks are now no-ops. Rough net: 2-6 real LLM calls/hour instead of 12.
- **Ignored zones are honored everywhere in the control loop.** `exclude_from_metrics` was previously only respected by analytics; it's now honored by `execute_active_mode`, `execute_follow_me_mode`, and every path through `apply_offset_compensation` (via `_fetch_zones` in `temp_compensation`). Mark the basement/attic/exercise room as excluded and their readings genuinely stop influencing decisions.

## [1.0.55] - 2026-07-04

### Fixed
- **Dashboard status label now reflects the actual system mode** instead of always showing "Following Schedule" whenever no manual override is active. The label was ignoring `current_mode` entirely, so toggling to Active or Follow Me made no visible change in the status bar even though the mode had actually switched (and the backend was running the AI controller every 5 min). New copy: `Override Active` → `AI Control` (active) → `Follow Me` (follow_me) → `Learning` (learn) → `Following Schedule` (scheduled). The mode-switcher lane buttons in the header already indicated the real mode; this just brings the dashboard status pill in line with them.

## [1.0.54] - 2026-07-04

### Fixed
- **GPT-5 (and other picky models) no longer crash chat with UnsupportedParamsError.** GPT-5 rejects `temperature != 1`; we were sending `temperature=0.7` from the default LLMProvider and LiteLLM raised before we could recover. Set `litellm.drop_params = True` at import time so LiteLLM silently drops any model-specific unsupported params (temperature, top_p, etc.) instead of erroring. Works for any picky model — GPT-5, o1, o3, Anthropic reasoning variants, etc.

## [1.0.53] - 2026-07-03

### Fixed
- **Pre-conditioning is now much more aggressive on hot (and cold) days** so schedules like "kid's room = 70°F at 6 PM" are actually met when outdoor temp is fighting the AC. `PatternEngine.get_preconditioning_time()` now:
  - Scales the effective cooling/heating rate *down* proportionally to how far outdoor temp is beyond a 3°C tolerance band (3% per °C, floored at 30% of nominal). Hostile outdoors → longer lead time.
  - Raises the maximum lead time from **2 hours → 3 hours** so extreme heatwaves + wide temperature gaps get a real head start.
  - Kicks in for smaller gaps (0.15°C, was 0.3°C).
- Example: 78°F → 70°F with 32°C outdoor now schedules ~122 min of pre-conditioning (was ~94 min); 27°C → 21°C with 38°C outdoor now schedules the full 180 min. On mild days behavior is essentially unchanged.
- Additive outdoor bump (~0.5 min per °C of adverse delta beyond 3°C) preserved as a small early-start bias on top of the rate scale.

## [1.0.52] - 2026-07-03

### Fixed
- **Chat with Ollama no longer returns the tool schema as text.** Small local models (qwen 4B, llama 3.1 8B, etc.) cannot reliably emit OpenAI-style tool_calls — many of them dump the tool schema back as plain text instead. Chat responses came back like "The tools available are: schedule_control, set_zone_temperature…" which is worse than useless. v1.0.49's retry only caught *empty* responses; echoed-schema responses passed through. Added `LLMProvider.TOOL_CAPABLE_PROVIDERS = {anthropic, openai, gemini, deepseek, grok}` and now silently drop `tools=[...]` for any provider outside that set. Chat becomes a Q&A/advice assistant on Ollama and llama.cpp; tool-driven actions still work on cloud providers.
- Advisor + active-mode paths were already tool-free (they ask for JSON), so they're unaffected.

## [1.0.51] - 2026-07-02

### Fixed
- **Active AI mode (the 5-minute autopilot) now uses the user's chosen LLM.** `execute_active_mode` in `backend/api/main.py` was hard-coded to try `anthropic` → `openai` → `gemini` and silently returned "No LLM provider configured" for users on Ollama, llama.cpp, DeepSeek, or Grok. Now delegates to `chat.py:get_llm_provider(db)`, so it respects `SystemConfig.llm_settings` and falls back through the full provider chain (including the local providers). This completes the "chat + advisor + active-mode" trio — every LLM caller in the addon now works with Ollama.

## [1.0.50] - 2026-07-02

### Fixed
- **Ollama chat + tool calling now actually work with llama 3.1, qwen 3, mistral, etc.** Both LLM provider paths (`LLMProvider._chat_once` for the chat route, and `ClimateIQLLMProvider._litellm_model` for the advisor) were sending `ollama/<model>` to LiteLLM. That prefix routes to Ollama's legacy `/api/generate` endpoint which silently drops the `tools=[...]` parameter and returns empty content whenever a model would have wanted to tool-call. Switched both paths to `ollama_chat/<model>`, which hits `/api/chat` — LiteLLM's Ollama tool-calling path. Plain-text chat and dashboard brief now work for any modern Ollama model; tool-capable Ollama models (llama3.1, qwen2.5:7b+, mistral-nemo, etc.) can now invoke ClimateIQ tools directly.

## [1.0.49] - 2026-07-02

### Fixed
- **Chat + dashboard brief work with small Ollama models (qwen 3.5 4B, llama 3.1 8B, etc.).** These models don't reliably follow the OpenAI function-calling schema, so when the chat route sent `tools=get_climate_tools()` they returned empty content with no `tool_calls`, and the response fell through to the canned "I'm not sure how to help with that." message. `_run_llm_with_tools` now retries once without tools when the first turn returns nothing, so a small local model can still answer plain-text questions like the dashboard's "brief summary" prompt. Tool-capable providers (Anthropic, GPT-4o, etc.) are unaffected — the retry only fires when the first response was empty.

## [1.0.48] - 2026-07-02

### Fixed
- **Advisor + auto-select LLM path now uses Ollama.** v1.0.47 fixed the chat route to reach Ollama, but the climate advisor path (`_build_llm_provider` in `backend/core/climate_advisor.py`) still built `ProviderSettings` without threading `settings.ollama_url` / `settings.llamacpp_url` as `base_url`. LiteLLM then defaulted to `http://localhost:11434`, which inside the HA add-on container is the container itself → `Errno 111 Connection refused`. The advisor now passes the configured local base URL for the primary provider and for any local-provider fallback, matching the chat path.
- **Settings UI now reports Ollama / llamacpp as configured** when a base URL is set. `llm_provider_config` in `backend/config.py` was hard-coding `configured: False` for both local providers.

## [1.0.47] - 2026-07-02

### Fixed
- **Chat now works with Ollama, llamacpp, DeepSeek, and Grok.** The chat route was still on the old simple `LLMProvider` that (a) only checked hardcoded keys (`anthropic` → `openai` → `gemini`), ignoring the user's provider choice saved to `SystemConfig.llm_settings`, and (b) never applied the `ollama/` / `deepseek/` / `grok/` prefix that LiteLLM needs for local + newer cloud providers. Chat requests fell back to Anthropic/OpenAI/Gemini even when Ollama was configured as primary; if none of those had a key, chat returned an empty 503.
  - `get_llm_provider()` now consults `SystemConfig.llm_settings.provider`/`model` first, then builds a fallback chain across every configured cloud key + configured local URL. All chat callers now pass the DB session so this lookup can happen.
  - Simple `LLMProvider` gained `base_url` support (for Ollama/llamacpp), a `grok`/`ollama`/`llamacpp` entry in `PROVIDER_MODELS`, and the correct `provider/model` prefix routing in `_chat_once()`.
  - llama.cpp's OpenAI-compatible endpoint is routed via LiteLLM's `openai/` prefix with the user's base URL.

### Added
- **Weather-aware schedule pre-conditioning.** The scheduler already started HVAC ~15 minutes ahead of a scheduled target, but the lead time was static — a hot day + upcoming cool target could easily miss because the pre-condition window was too short. Pre-conditioning now factors in:
  - The zone's *current* temperature gap to the schedule target.
  - The zone's learned `heating_rate_c_per_hour` / `cooling_rate_c_per_hour` (from `zone_analytics.py`).
  - Current outdoor temperature (from the weather cache) — with a bump of ~0.5 min per °C of adverse outdoor delta beyond a 3°C tolerance band. Hostile outdoor conditions push the pre-condition window earlier so schedules are actually met.
  - Lead time is clamped to `[5, 120]` minutes to prevent runaway values on cold outages.
- `PatternEngine.get_preconditioning_time()` gained optional keyword arguments (`current_temp_c`, `target_temp_c`, `outdoor_temp_c`, `hvac_mode`, `thermal_profile`). Falling back to the legacy static formula is unchanged, so anything that doesn't pass the new args behaves as before.

### Fixed (CI)
- Two `Unused "type: ignore"` errors from the v1.0.46 CI run cleaned up (`backend/core/seasonal_lock.py:271` assignment ignore was redundant; `backend/api/routes/system.py:911` was rewritten to avoid the `Awaitable[bool] | bool` union that needed the ignore). CI now goes green again alongside the addon build.

## [1.0.46] - 2026-05-18

### Added
- **Seasonal HVAC lock with outdoor-temperature safety override.** Configure the year as a set of seasons (Winter / Spring / Summer / Fall by default) and lock each to a preferred direction — `heat`, `cool`, or `auto`. While a season's lock is active, the auto-select engine refuses to flip the thermostat to the opposite mode and the LLM advisor's `hvac_mode` recommendation is filtered to match. This is the user-requested fix for "it's summer, stop switching back and forth between heat and cool — stay on cool."
- **Outdoor-temperature escape valve per season.** Each locked season can declare a one-way override: a *cool* season allows heat when the outdoor temp drops below a threshold (default ≈ 40 °F for Summer); a *heat* season allows cool when outdoor rises above a threshold (default ≈ 70 °F for Winter). The override consults the configured `weather_entity` and stays out of the way when no entity is set.
- **Settings → Modes → Seasonal Lock** card: enable/disable, edit season names + date ranges, pick preferred mode per season, and set override thresholds in the user's display unit. Shows the live status (active season, locked mode, override active).
- **Dashboard status bar**: now shows the active season + lock direction (e.g. "Season: Summer · locked to cool") when seasonal lock is enabled.

### Backend
- New module `backend/core/seasonal_lock.py` (Pydantic models, year-wrap-aware season detection, outdoor reader mirroring the climate-advisor pattern).
- New endpoints: `GET /settings/seasonal-lock` (returns `{config, state}`) and `PUT /settings/seasonal-lock`.
- `seasonal_lock` is wired into `_auto_select_hvac_mode` *after* the explicit `hvac_control_mode` check — explicit user choice always wins, the seasonal lock layers in next, then the sensor-driven mode picker.
- Wrong-direction detection in auto-select extends to the seasonal lock: if the thermostat is on the *opposite* mode of the lock, the switch happens immediately without dead-band or cooldown gating.

## [1.0.45] - 2026-05-18

### Fixed (CI)
- Backend mypy was failing on 13 errors: two real `Returning Any` issues in `temp_compensation.py` (now wrapped with explicit `str()` / `float()` coercion), one missing generic in `api/main.py` (`dict` → `dict[str, Any]`), and ten `unused "type: ignore"` warnings (six `[arg-type]` ignores in `decision_engine.py`, one `[attr-defined]` in `climate_advisor.py`, one `[union-attr]` in `zones.py`, and three `[union-attr, attr-defined]` in `api/main.py` reduced to just `[attr-defined]`). No runtime behavior change.

## [1.0.44] - 2026-05-18

### Fixed
- **DeepSeek API key never reached the backend** — `climateiq/run.sh` reads each LLM key from `/data/options.json` and exports it as `CLIMATEIQ_<NAME>_API_KEY` for Pydantic settings. The DeepSeek wiring was missing: the script wasn't parsing `.deepseek_api_key` or exporting `CLIMATEIQ_DEEPSEEK_API_KEY`. So even though the field showed up in the addon UI, `SETTINGS.deepseek_api_key` was always empty and the Providers tab showed DeepSeek as **Not set** (no green dot). Now wired correctly.
- **Ollama/llamacpp always appeared "Configured"** — `_resolve_provider_credentials` did `str(SETTINGS.ollama_url) or None`, but `str(None)` returns the literal string `"None"` (truthy). Replaced with `_normalize_local_url()` which treats `None`, empty string, and `"None"` as unconfigured. Also changed the defaults in `backend/config.py` for `ollama_url` and `llamacpp_url` from `http://localhost:...` to `""` so an unset URL means unset.

### Changed (LLM Settings UI)
- **"Currently in use" banner** at the top of the LLM Providers tab now shows the actual primary provider/model from `SystemConfig.llm_settings` (fetched via `GET /system/config/llm`). Before this, there was no way to see which provider was selected as primary — multiple providers showed green "Active" badges meaning "has API key," not "currently selected."
- **"Primary" badge** on the currently-selected provider card (separate from the "Configured" badge).
- **"Configured"** label replaces the misleading **"Active"** label.
- **"Save as Primary"** button (renamed from "Save"). Disabled until a model is selected, preventing the silent fallback to `gpt-4o-mini`.
- The tab now **auto-selects** the current primary on load (uses render-time sync per React 19 guidance, not `useEffect` + `setState`).

> **NOTE for upgrade**: If you had DeepSeek configured before and saw "Not set" — that was the run.sh wiring gap fixed in this release. After upgrading and restarting, DeepSeek should now show "Configured" (green) once your key is filled in.

## [1.0.43] - 2026-05-17

### Fixed
- **DeepSeek provider selection ignored** — the LLM provider builders in `climate_advisor.py` and `decision_engine.py` were missing `deepseek` (and `llamacpp`) in their api-key lookup maps. Even when DeepSeek was set as the primary in `SystemConfig.llm_settings`, the key was never wired up, so the chain fell back to Ollama/OpenAI.
- **Hardcoded OpenAI fallback when primary fails** — the secondary provider was always built as OpenAI `gpt-4o-mini` if (and only if) the OpenAI key was present. If the user had only DeepSeek and Anthropic keys, primary failures had no fallback. Both builders now auto-detect the first configured cloud provider (prefer the historical OpenAI default when its key exists; otherwise scan Anthropic → OpenAI → Gemini → DeepSeek → Grok).

> **NOTE for upgrade**: the code fix wires the key correctly, but the *active provider* still lives in `SystemConfig.llm_settings`. If your DB still says `provider=ollama`, change it under **Settings → LLM** in the UI (e.g. to `deepseek` + `deepseek-chat`) for the new behavior to take effect.

## [1.0.42] - 2026-05-17

### Fixed
- **Thermostat-anchor double-counting** — when the configured `thermostat_temp_sensor` override entity is also a member of one of the active zones, that sensor is now excluded from zone-average and priority-zone computations. The anchor reading still drives the offset formula's base, but the zone side reflects the *other* sensors only. Prevents the cooling-direction artifact where the offset bias shifts as soon as the thermostat-anchor sensor's own room cools faster than the rest of the schedule.

## [1.0.41] - 2026-05-17

### Fixed
- **Setpoint reconciliation** — `maintain_climate_offset` now reads HA's actual current setpoint each tick and writes when reality drifts from the expected value, not only when our last-write cache disagrees. If Ecobee, HomeKit, or a user overrides the setpoint behind our back, the next tick reasserts it.
- **Heat_cool / auto-mode dual setpoint routing** — `set_temperature` now accepts an `intent_mode` hint. When the thermostat is in heat_cool or auto, cool-mode writes update `target_temp_high` (and adjust `target_temp_low` to maintain Ecobee's 2° spread); heat-mode writes update `target_temp_low`. Previously every dual-setpoint write went to `target_temp_low`, breaking cool intent.
- **Override status display** — `/system/override` now falls back to `target_temp_high` (cool) or `target_temp_low` (heat) when the climate entity reports neither `temperature` nor a single-setpoint mode. Dashboard "thermostat" reading no longer goes blank when the thermostat is in heat_cool.

## [1.0.40] - 2026-05-17

### Changed
- **Token-spend trim across LLM calls** — no behavior changes, no model swaps, just smaller payloads:
  - **Chat `SYSTEM_PROMPT`** condensed from ~40 lines to ~15. Same data-integrity rules and save_memory guidance, shorter wording. Section headers compacted.
  - **`_get_logic_reference_text()`** collapsed from 8 multi-bullet sections (~40 lines) to one paragraph per topic (~8 lines). Same anchor info — the LLM still answers "how does X work?" questions; deeper detail is on tap via tool calls.
  - **Chat history cap**: `limit(10)` → `limit(6)`. Most queries don't reference >3 exchanges back; saves ~40% of history tokens.
  - **`get_conditions_context`** sensor reading lookup: `limit(25)` → `limit(5)`. The loop already breaks early once temp+humidity+presence are filled; 25 was over-fetching by ~5×. DB win and slightly smaller payload.
  - **Directive extraction** "already saved" block: capped at 20 most-recent (was 50, oldest-first), per-directive snippet 120→100 chars. Stops the prompt from growing unbounded as memories accumulate.
  - **ClimateAdvisor device-action timestamps**: `isoformat()` (with microseconds) → `%Y-%m-%d %H:%M`. ~20 chars saved per action × 5 actions per advisor tick. Decisions never use subsecond precision.

## [1.0.39] - 2026-05-17

### Added
- **DeepSeek as an LLM provider**: `deepseek` is now in `SUPPORTED_PROVIDERS` alongside Anthropic, OpenAI, Gemini, Grok, Ollama, and llama.cpp. New `deepseek_api_key` setting on the backend (Pydantic `Settings`) and in the HA add-on `config.yaml` schema (options + schema). LiteLLM model strings are constructed as `deepseek/<model>` (e.g. `deepseek/deepseek-chat`, `deepseek/deepseek-reasoner`). Model discovery hits DeepSeek's OpenAI-compatible `https://api.deepseek.com/v1/models` endpoint with `Authorization: Bearer <key>`; on failure the UI still gets `deepseek-chat` and `deepseek-reasoner` as fallback options. The Settings → LLM Providers tab picks up DeepSeek automatically — the frontend list is driven by the backend `/settings/llm/providers` response, no UI changes were needed.

## [1.0.38] - 2026-05-17

### Fixed
- **`send_chat_message` crashed with `UnboundLocalError: conversation`** on dashboard chat calls. Dashboard chats skip persistence (`is_dashboard_call`), so the `conversation` local was never assigned — yet `_extract_directives` and the response `metadata` both referenced `conversation.id` unconditionally. Now `conversation` is initialized to `None`, directive extraction is skipped for non-persisted (dashboard) chats, and `metadata` is `{}` when there is no conversation row to reference. Persisted (regular chat) calls behave identically. Surfaced when an upstream LLM request itself failed (Gemini free-tier 429), which masked the real bug — but the dashboard path was always crashing on this line regardless of the LLM outcome.

## [1.0.37] - 2026-04-24

### Added
- **HVAC control mode setting (Auto / Heat Only / Cool Only)**: New `hvac_control_mode` setting lets users lock the thermostat to a single direction (heat or cool) or leave it on `auto` so ClimateIQ chooses based on zone temps vs target. When locked, `_auto_select_hvac_mode` short-circuits and returns the user's chosen mode (still honoring thermostat support — falls back gracefully if the mode isn't in the climate entity's `hvac_modes`). The lock treats current `auto`/`heat_cool` or opposite-direction state as urgent and bypasses the cooldown so the requested mode takes effect immediately.
- **Configurable mode-switch cooldown (`mode_switch_cooldown_minutes`)**: Replaces the hardcoded 30-minute cooldown with a user-tunable value (0–240 minutes; 0 disables the cooldown). All four call sites (`execute_schedules`, `apply_schedule_now`, `maintain_climate_offset`, advisor flow) now read the configured value and pass it to `_switch_hvac_mode_if_needed`. The wrong-direction override still bypasses the cooldown when the thermostat is actively working against the target.
- **Settings UI**: New "HVAC Control" card on the Logic tab with a 3-up Auto / Heat Only / Cool Only selector and a number input for the cooldown.

## [1.0.36] - 2026-04-25

### Fixed
- **Removed broken "Save HA Settings" button**: Clicking it produced "No fields provided for update" because the URL/token fields are sourced from the add-on's environment variables (CLIMATEIQ_HA_URL, CLIMATEIQ_HA_TOKEN) and were never wired through the settings PUT schema. The Home Assistant URL and token fields are now read-only display values with helper text pointing users to the add-on configuration page in HA. The "Test Connection" button is preserved.

## [1.0.35] - 2026-04-25

### Added
- **Thermostat temperature sensor override**: New optional setting `thermostat_temp_sensor` lets users designate any HA sensor entity as the thermostat's "current temperature" source for offset compensation. Workaround for the Ecobee/HomeKit integration bug in HA where the climate entity's `current_temperature` attribute stops refreshing. When set, `get_thermostat_reading_c` reads from the override sensor (parsed via the same `_parse_temp_from_state` helper used for zone sensors, so unit detection and multisensor formats are handled). Falls back to the climate entity's reading when the override sensor is unset, missing, or unparseable. Configurable in Settings → Home Assistant tab.

## [1.0.34] - 2026-04-24

### Fixed
- **Schedule's stored `hvac_mode` no longer locks the thermostat to a direction**: A schedule with `hvac_mode="heat"` was bypassing auto-select entirely, so a 68°F target schedule would keep the thermostat in heat mode even when the house was at 77°F. The schedule's `hvac_mode` field is now ignored for direction (heat/cool/auto/heat_cool all route through `_auto_select_hvac_mode` from live zone sensors). Only `"off"` is still honored — explicitly turning the system off during a schedule remains a valid intent. Existing schedules don't need to be edited; the stored value is simply no longer authoritative.

## [1.0.33] - 2026-04-24

### Changed
- **Never use the thermostat's built-in auto / heat_cool mode**: ClimateIQ now always commits to an explicit `heat` or `cool` direction — the thermostat is never left in Ecobee's `auto` or HA's `heat_cool` where the thermostat itself decides which side to drive. `_auto_select_hvac_mode` no longer falls back to `heat_cool` when `heat`/`cool` aren't in the supported modes (it logs a warning and leaves the mode alone). When the thermostat is observed sitting on `auto`/`heat_cool`, the system now switches it to heat or cool based on the sign of the zone error (bypassing the cooldown — same urgency flag used for the wrong-direction override). Schedules with `hvac_mode="heat_cool"` now route through auto-selection just like `hvac_mode="auto"` does. The AI advisor prompt and validator now reject `heat_cool`; only `heat` or `cool` are accepted.

## [1.0.32] - 2026-03-08

### Fixed
- **AI advisor mode recommendation now gated by dead-band**: The advisor's optional `hvac_mode` field was previously applied unconditionally, allowing the AI to trigger a mode switch (e.g. cool) for a 0.5°F overshoot even when the rule-based path correctly suppressed it. The advisor's recommendation is now ignored when the zone is within 0.6°C (~1°F) of target — the same threshold used by `_auto_select_hvac_mode`. Suppressed recommendations are logged at DEBUG level.
- **Tightened advisor prompt for mode switches**: The HVAC MODE prompt section now explicitly requires (1) zone >1°F from target, (2) outdoor conditions that support the switch — e.g. do not recommend cool when it is ≤50°F outside since passive correction is more efficient, and (3) the current mode cannot reach the target. The prompt now states: "A missed mode switch costs less than an unnecessary one."

## [1.0.31] - 2026-03-08

### Fixed
- **Schedule notifications show the schedule target, not the thermostat setpoint**: The push notification for a schedule activation previously showed the offset-adjusted thermostat setpoint (e.g. "71°F") rather than what the schedule is actually targeting (e.g. "68°F"), which was confusing. Notifications now read "Set to 68°F" using the schedule's configured target temperature. When offset compensation is active (|offset| > 0.1°C), the adjusted thermostat value is appended in parentheses — e.g. "Set to 68°F (thermostat adjusted to 71°F)" — so the offset is visible but not the headline. Logs were updated to show both values as `target=X thermostat=Y` for clarity.

## [1.0.30] - 2026-03-08

### Fixed
- **Wrong-direction mode override**: When the thermostat is actively working against the target (e.g. running cool while zone sensors are already below the schedule target), the system now switches to the correct mode immediately — bypassing both the 1°F dead-band and the 30-minute cooldown. This is not oscillation; it is the thermostat moving temps in the wrong direction. `_auto_select_hvac_mode` returns an `urgent=True` flag in this case; callers pass `override_cooldown=True` to `_switch_hvac_mode_if_needed`, which logs the switch with a `[wrong-direction override]` marker. The cooldown and dead-band remain fully active for normal near-target transitions.

## [1.0.29] - 2026-03-08

### Added
- **Mode switch cooldown**: `_switch_hvac_mode_if_needed` now enforces a 30-minute cooldown between mode reversals (e.g. heat → cool or cool → heat). Switching to the same mode is always allowed (idempotent). When a reversal is blocked, the suppression and remaining cooldown are logged at INFO level. This applies to both the rule-based auto-select path and the AI advisor recommendation path.
- **Wider auto-select dead-band**: The threshold in `_auto_select_hvac_mode` for triggering a mode switch was increased from 0.3 °C (~0.5 °F) to 0.6 °C (~1 °F). Zones must be at least 1 °F below target before switching to heat, or 1 °F above before switching to cool, preventing switches near the target temperature.

## [1.0.28] - 2026-03-08

### Fixed
- **Thermostat sensor never used as zone temperature**: `_auto_select_hvac_mode` no longer falls back to the Ecobee's own sensor when zone readings are unavailable. The thermostat sensor is only valid for offset compensation (computing how far to push the setpoint) — it must not stand in for actual zone temperatures. If zone sensor data is absent, the function now returns `None` and leaves the current HVAC mode unchanged rather than making a potentially wrong decision based on the hallway/return-air sensor.

## [1.0.27] - 2026-03-08

### Fixed
- **Auto HVAC mode selection now uses zone sensor average**: `_auto_select_hvac_mode` was comparing the schedule target against the thermostat's own sensor (hallway/return), which can read warmer than the actual zones being targeted. If the Ecobee hallway read at or above the schedule target while bedroom sensors were below it, `error_c` would be ≤0 and the function would return `None` — leaving the thermostat in whatever mode it was already in even though heat was needed. Fixed by passing `db` and `zone_ids` to the function so it reads the zone sensor average (same source used by offset compensation) and falls back to the thermostat reading only when zone sensors are unavailable.

## [1.0.26] - 2026-03-08

### Changed
- **Weather-aware HVAC mode via AI advisor**: The climate advisor LLM now receives an optional `hvac_mode` field in its JSON response schema. When outdoor conditions make a mode switch clearly beneficial (e.g. very cold outside with zones drifting below target → recommend `"heat"`; warm outside with zones climbing → recommend `"cool"`), the advisor includes it and the system switches the thermostat accordingly. This overrides the initial rule-based auto-selection so the AI — which already has full outdoor temperature and condition context — makes the final call. The rule-based `_auto_select_hvac_mode` remains as a fallback for ticks where the advisor is skipped (dead-band path). Omitting `hvac_mode` leaves the current thermostat mode unchanged.

## [1.0.25] - 2026-03-08

### Changed
- **Automatic HVAC mode selection**: When a schedule's `hvac_mode` is `"auto"` (the default), the system now automatically switches the thermostat to `heat` or `cool` based on whether the current temperature is below or above the target — rather than leaving it in whatever mode it happened to be in. A ±0.3 °C (~0.5 °F) dead-band prevents oscillation when the thermostat is near the target. If only `heat_cool` is supported (no dedicated heat/cool modes), that is used as the fallback. Explicit schedule modes (`heat`, `cool`, `heat_cool`, `off`) continue to work as before.

## [1.0.24] - 2026-03-08

### Added
- **Schedule-driven HVAC mode switching**: The thermostat's heat/cool/heat_cool/off mode is now switched to match the schedule's `hvac_mode` before the temperature setpoint is applied. Previously the `hvac_mode` field was stored in the database and returned in API responses but never actually sent to the thermostat. Mode switching is wired into `execute_schedules()`, `apply_schedule_now()`, and `maintain_climate_offset()`. A schedule set to `"auto"` continues to leave the thermostat's current mode unchanged (only the temperature is adjusted). The switch only issues an HA service call when the thermostat is not already in the target mode, avoiding unnecessary traffic on every 60-second tick.
- `apply_offset_compensation()` now accepts an optional `hvac_mode` parameter so callers that have just switched the mode can pass the known target mode directly, avoiding a stale thermostat state read from HA immediately after the switch.

## [1.0.23] - 2026-02-27

### Fixed
- **Offset compensation overshoot bug**: When zone sensors were already above the schedule target in heat mode, the thermostat anchor logic was pushing the setpoint *higher* than desired (e.g. schedule=68°F, zones=69.8°F, thermostat hallway=72°F → adjusted=70°F). The anchor used the thermostat reading as the base (72°F) and only subtracted the small zone error (-2°F), landing at 70°F instead of ≤68°F. Fixed by adding a fast-path in `compute_adjusted_setpoint`: when zones are at or above target in heat mode (or at/below in cool mode), the setpoint is immediately clamped to `min(desired, thermostat_reading)` — guaranteeing the thermostat is already satisfied and HVAC stops. This also handles the edge case where the thermostat sensor itself reads below the target in a warm room (sets setpoint = thermostat's current reading → immediately satisfied → no further heating).

## [1.0.22] - 2026-02-26

### Fixed
- **Immediate schedule application**: When a schedule is created, updated, or enabled, the thermostat is now set immediately if the schedule is currently active (correct day, current time is within the schedule window). Previously the system waited up to 60 s for the next `execute_schedules()` tick. The new `apply_schedule_now()` function runs as a fire-and-forget background task so the API response is still instant.

## [1.0.21] - 2026-02-26

### Fixed
- **Chat tool-call follow-up**: When Claude returned preamble text ("I'll check...") alongside tool calls, the follow-up analysis was never made — the user saw only the preamble with no actual answer. The follow-up LLM call is now always made whenever any tools were used, regardless of whether the first turn included text. The preamble is correctly passed as the assistant's `content` in the follow-up context.

## [1.0.20] - 2026-02-26

### Improved
- **Climate Advisor LLM context**: Advisor prompt now includes live zone occupancy (last 30 min), recent comfort feedback (last 48h), upcoming schedule changes (next 2h), and sensor health (stale sensor flags). Decisions are now aware of who is home, how comfortable people have been, and what the schedule is about to do.
- **Decision Engine LLM prompt**: Rebuilt from a one-line placeholder into a structured prompt with zone name, current/target temps in both C and F, delta, HVAC mode, humidity, occupancy, and extra metrics. LLM now returns structured JSON (`{"action": "...", "reason": "..."}`); keyword matching is kept as fallback.
- **Directive extraction deduplication**: Before mining a conversation for new house facts, the extractor now loads all existing active directives and appends them to the prompt as "ALREADY SAVED" — preventing the LLM from re-extracting information already in memory.

## [1.0.19] - 2026-02-26

### Added
- **`get_zones` chat tool**: LLM can now list all configured zones with current temperature, humidity, occupancy, floor, sensor count, device count, and last reading timestamp — all in one call.
- **`get_devices` chat tool**: LLM can now query all HVAC/thermostat devices — type, HA entity ID, zone assignment, primary status, and capabilities.
- **`get_energy_data` chat tool**: LLM can now estimate HVAC energy usage (kWh and cost) from device action history, broken down per zone. Useful for energy efficiency questions.
- **`get_comfort_scores` chat tool**: LLM can now compute comfort scores (0–100) per zone based on how often temperature and humidity stayed in the comfort range over a given window.
- **`set_system_mode` chat tool**: LLM can now switch the ClimateIQ operating mode (learn, scheduled, follow_me, active) with a reason.
- **`set_override` chat tool**: LLM can now set a manual temperature override directly on the thermostat. Temperature is provided in the user's display unit; the tool handles conversion.
- **`cancel_override` chat tool**: LLM can now cancel any active manual hold, returning the thermostat to its normal schedule (uses Ecobee `resume_program` with non-Ecobee fallback).
- **`delete_schedule` chat tool**: LLM can now delete a schedule by ID (use `get_schedules` first to find the ID).
- **`delete_directive` chat tool**: LLM can now delete a saved memory/directive by ID or exact text — lets users ask the assistant to forget specific facts.

## [1.0.18] - 2026-02-26

### Added
- **`get_zone_history` now supports all zones**: `zone_id` is now optional. Omitting it returns history for every active zone in a single call, eliminating the need for N sequential calls to compare rooms (e.g. "how did all zones do overnight?").
- **`get_schedules` chat tool**: LLM can now query all configured temperature schedules — name, target temp in display unit, HVAC mode, days of week, start/end times, priority, and which zones they apply to. Answers questions like "what's my sleeping schedule?" or "what runs at night?"
- **`get_user_feedback` chat tool**: LLM can now query comfort feedback history (too_hot, too_cold, too_humid, too_dry, comfortable) per zone with a summary by type. Reveals recurring comfort issues the system should address.
- **`get_sensor_status` chat tool**: LLM can now inspect all sensors — zone assignment, HA entity ID, `last_seen` timestamp, minutes since last reading, active status, and calibration offsets. Useful for diagnosing stale/offline sensors affecting data quality.
- **`get_occupancy_patterns` chat tool**: LLM can now access learned occupancy patterns per zone (pattern type, season, confidence, schedule data), enabling smarter scheduling recommendations.
- **`get_ai_decisions` chat tool**: LLM can now read the AI advisor's full decision log — action taken, trigger reason, setpoint commanded, and complete reasoning text. Lets users ask "why did the system do X?" and get a real answer.

## [1.0.17] - 2026-02-26

### Added
- **`get_zone_history` chat tool**: LLM can now query historical temperature and humidity for any zone over a configurable time window (default 8 hours). Returns avg/min/max temps, drift (variation), and an hourly breakdown in the user's display unit. Answers questions like "how well was the bedroom maintained overnight?" or "was there a temperature swing last night?"
- **`get_device_actions` chat tool**: LLM can now query recent HVAC thermostat commands for any zone (or all zones). Returns action type, trigger reason, setpoint commanded, timestamp, and the AI's reasoning — giving full visibility into what the system did and why.

### Changed
- **`get_zone_status` enriched**: Now returns zone name, temperature in display unit (°F/°C), humidity, presence, last reading timestamp, and the most recent HVAC action with its setpoint and reasoning — instead of raw Celsius-only values.

## [1.0.16] - 2026-02-26

### Fixed
- **Impossible temperature warnings from thermostat entity**: `climate.thermostat` (Ecobee) never includes `unit_of_measurement` or `temperature_unit` in its HA attributes, so the WebSocket parser had no unit signal and treated 68°F as 68°C — which was then correctly dropped as "impossible". Fixed by passing the system-configured HA temperature unit (`ha_temp_unit`) to `HAWebSocketClient` at startup. When an entity's attributes carry no explicit unit, the parser now falls back to the configured HA unit instead of assuming Celsius.
- **Chat tool calls returning no data**: When the LLM called a data-fetching tool like `get_zone_status` or `get_weather`, the results were collected internally but never fed back to the LLM. The LLM had nothing to formulate a response from, so it silently stopped. Implemented proper multi-turn tool calling: after executing tool results, if the LLM returned no text content, a follow-up call is made with the tool results appended so the LLM can produce a natural-language answer. Affects both the REST and WebSocket chat paths.

## [1.0.15] - 2026-02-25

### Fixed
- **Dashboard "What's Happening" placeholder text**: Placeholder said "Click ↑ to generate a summary" but the button uses a circular refresh icon, not an up arrow. Updated to "Click the refresh button to generate a summary."

## [1.0.14] - 2026-02-25

### Fixed
- **Dashboard "What's Happening" calls polluting Chat history**: Every click of the refresh button on the dashboard summary card sent a POST to `/api/v1/chat`, which created a `Conversation` record that appeared as a session in the Chat sidebar. The dashboard now sends `context: { source: "dashboard" }` with its request, and the backend skips persisting any conversation where `context.source == "dashboard"`. The summary still works, but nothing is written to the `conversations` table.

## [1.0.13] - 2026-02-25

### Fixed
- **LLM calling `save_memory` when asked to list memories**: When a user asked "what's in memory?", the LLM was calling `save_memory` 4 times (re-saving already-saved directives) instead of reading and listing from the `<user_directives>` block already in its context. The MEMORY SYSTEM prompt now explicitly separates READ (summarise from `<user_directives>`) from WRITE (`save_memory` tool, for NEW information only). The LLM is now instructed never to call `save_memory` to confirm, re-save, or list existing memories.
- **No text response when all save attempts are duplicates**: The backend fallback message synthesis only fired when at least one memory was newly saved. When every attempt was a duplicate (`saved=False`), the fallback was skipped and the user saw no text response at all — only silent action chips. Added an `elif skipped` branch that generates "These memories are already saved — nothing new to add."

## [1.0.12] - 2026-02-25

### Fixed
- **mypy: `zone_id` redefinition in `_execute_tool_call`**: The `save_memory` branch re-declared `zone_id` with a type annotation in the same function scope as the `set_zone_temperature` branch, causing a `[no-redef]` error. Renamed to `mem_zone_id` to isolate the two branches.
- **mypy: `ha_url`/`ha_token` not on `Settings`**: `system.py` was accessing non-existent `_cfg.ha_url` and `_cfg.ha_token`. Corrected to `home_assistant_url` and `home_assistant_token` (the actual `Settings` field names).
- **mypy: Schedule query typed as `SystemSetting`**: Reusing the `result` variable for both a `SystemSetting` query and a subsequent `Schedule` query caused mypy to infer the wrong element type, flagging `schedule.days_of_week` etc. as missing. Renamed the Schedule query variable to `sched_result`.
- **mypy: `val` type reuse in `sensors.py`**: `val` was first assigned `float` via `float(state.state)`, then reassigned `Any | None` via `attrs.get()` in fallback loops. Renamed to `attr_val` in the fallback blocks.
- **mypy: `col_attr: object` lacks `.isnot()`**: Parameter typed as `object` in `zones.py` — changed to `Any` so SQLAlchemy column methods are accessible without error.
- **mypy: `float | None` multiply in `zone_analytics.py`**: `_safe_mean()` returns `float | None`; the result was multiplied by a weight without a None guard. Added an explicit `if present_avg is not None` check instead of the incorrect `# type: ignore[arg-type]`.
- **mypy: `type: ignore[union-attr]` not covering `[attr-defined]`**: Three `db.execute()` calls in `main.py` and one in `zones.py` had ignore comments that didn't match the actual error code. Updated to `[union-attr, attr-defined]`.
- **mypy: `**dict[str, object]` in test helpers**: `_make_payload` in `test_schedule_helpers.py` returned `dict[str, object]`, making `ScheduleCreate(**payload)` fail strict type checking. Changed to `dict[str, Any]`.

## [1.0.11] - 2026-02-25

### Fixed
- **`save_memory` actions now show directive text in chat UI**: The "Actions taken" panel previously displayed repeated `save_memory` chips with no visible content — the user had no way to see what was actually stored. Action chips for `save_memory` now render the directive text and category (e.g. "Saved memory: Office occupied 9am-5pm weekdays [occupancy]"). Duplicate-skipped memories show the note text instead.
- **Chat response narrates saved memories when LLM omits text**: When the LLM calls `save_memory` but returns no accompanying text response, the backend now synthesises a confirmation message listing every directive that was saved (one bullet per item). This ensures the user always sees a readable acknowledgement regardless of LLM verbosity. System prompt updated with an explicit IMPORTANT instruction to always include a text confirmation after calling `save_memory`.

## [1.0.10] - 2026-02-25

### Fixed
- **Embedding provider falls back to Gemini when no OpenAI key**: All three embedding call sites (`_extract_directives`, `_get_relevant_directives`, `save_memory` tool handler) were gated on `openai_api_key`, so Anthropic-only users never got embeddings or semantic memory search. Extracted a `_get_embedding(text)` helper that tries OpenAI `text-embedding-3-small` (1536 dims) first, then Gemini `text-embedding-004` with `dimensions=1536` as fallback. Users with only Anthropic + Gemini now get full semantic memory search. Users with neither provider fall back to loading all memories (unchanged behaviour).

## [1.0.9] - 2026-02-25

### Added
- **`save_memory` LLM tool**: The chat AI can now explicitly save facts, routines, and preferences to permanent memory when a user asks it to. Previously the LLM told users it had no ability to save memories even though the system has a `user_directives` table — it just lacked a callable tool. Added `save_memory(directive, category, zone_name?)` to the tool schema in `tools.py`, wired up the handler in `_execute_tool_call`, and updated `SYSTEM_PROMPT` to explain the memory system and instruct the LLM to use the tool when asked. Automatic extraction still runs on every message; this tool covers explicit user requests and important facts the LLM wants to ensure are captured. Saved memories include embedding generation and deduplication, identical to the auto-extraction path.

## [1.0.8] - 2026-02-25

### Fixed
- **DB crash on startup**: `column user_directives.embedding does not exist` — `init_db()` now applies the embedding column migration inline (same pattern as other column migrations). The `embedding vector(1536)` column and its ivfflat index are added via `ALTER TABLE … ADD COLUMN IF NOT EXISTS` on startup, so the column exists before SQLAlchemy's ORM emits any SELECT against `user_directives`.

## [1.0.7] - 2026-02-25

### Added
- **Persistent house memory with semantic search**: Conversations now build a growing knowledge base about your home. The extraction prompt was broadened from HVAC action-preferences only to capture any house knowledge — zone characteristics ("south bedroom overheats in afternoon sun"), daily routines ("we wake up at 7am weekdays"), occupancy patterns ("office occupied 9am–5pm"), and household context ("we have a baby"). Three new memory categories added: `house_info`, `routine`, `occupancy`.
- **Vector embeddings on memories**: After each directive is extracted from conversation, an embedding is generated (via `text-embedding-3-small` when an OpenAI key is configured) and stored in a new `embedding vector(1536)` column on `user_directives` (migration `003_memory_embeddings`). An ivfflat index enables fast approximate nearest-neighbour search.
- **Semantic retrieval in climate advisor**: `ClimateAdvisor.advise()` now calls `_get_relevant_directives()` before building its prompt — performing a pgvector cosine similarity search against the current zone + HVAC context to surface the most relevant memories. A new "HOUSE KNOWLEDGE / USER PREFERENCES" section is injected into every advisor prompt. Falls back to loading all active memories when embeddings are unavailable.
- **"Add memory" form in chat sidebar**: Users can now type house facts directly into the Memories panel without needing a full conversation. Submits to the existing `POST /chat/directives` endpoint.
- **Enhanced Memories UI**: The sidebar panel is renamed from "Directives" to "Memories" with a subtitle ("Learned from conversations · used by the AI advisor"), color-coded category badges (blue=preference/comfort/constraint, green=routine/schedule/occupancy, amber=house_info, purple=energy), and the `·` separator replaces `--`.

## [1.0.6] - 2026-02-25

### Fixed
- **Skip secondary LLM provider when no API key configured**: All three `ClimateIQLLMProvider` builders (`decision_engine._build_llm_provider`, `decision_engine._get_configured_llm_provider`, `climate_advisor._build_llm_provider`) were unconditionally constructing a secondary OpenAI `ProviderSettings` even when no OpenAI key was set. This caused a wasted API attempt with an auth failure on every Anthropic overload event, plus a spurious WARNING log. Secondary is now only added when its key is actually present.

## [1.0.5] - 2026-02-25

### Fixed
- **LLM provider fallback on overload/errors**: When the primary provider (Anthropic) returns a 529 overloaded error or any other failure, the chat route now automatically retries with the next configured provider (OpenAI, then Gemini) before surfacing an error. Previously `LLMProvider.chat()` caught the exception only to re-raise it — no fallback occurred. Added a `fallbacks: list[LLMProvider]` field and a `_chat_once()` helper; `get_llm_provider()` now builds the full chain from all configured API keys rather than picking just the first available provider.

## [1.0.4] - 2026-02-25

### Fixed
- **HVAC status uses actual `hvac_action` from Home Assistant**: The heating/cooling/idle indicator on the Avg Temp card was previously derived by comparing the thermostat reading against the setpoint — a thermostat in `heat` mode but already satisfied would incorrectly show "— Idle" or vice versa. HA climate entities expose a dedicated `hvac_action` attribute (`heating`, `cooling`, `idle`, `off`, `fan`) that reflects what the system is actually doing right now. The backend now reads `attrs.get("hvac_action")` from the thermostat state and includes it in the `OverrideStatus` response; the Dashboard consumes it directly.

## [1.0.3] - 2026-02-25

### Added
- **HVAC status indicator on Avg Temp card**: The card now shows whether the system is currently **▲ Heating** (orange), **▼ Cooling** (blue), or **— Idle** (muted) below the "Set:" temperature. The thermometer icon and its background glow also update to match — orange when heating, blue when cooling, neutral when idle. Status is derived from `hvac_mode`, `current_temp` (thermostat reading), and `target_temp` (active setpoint) already present in `overrideStatus`; no backend changes required.

## [1.0.2] - 2026-02-25

### Fixed
- **LLM advisor blind to whether HVAC is actually running**: The LLM prompt showed thermostat reading and current setpoint as separate numbers but never stated their relationship — whether the HVAC was actively firing. In the observed case (thermostat 71°F, setpoint 70°F, heat mode) the LLM reasoned "the heating rate of +1.2°F/hour should close the gap" and returned `hold`, not realising that `thermostat > setpoint` means heat is OFF and the positive trend rate was historical, not current. Added an explicit `HVAC currently:` field to the prompt (e.g. `NO — thermostat (71°F) is at or above setpoint (70°F); heat will not run until setpoint > 71°F`) and a NOTE under the trend rate warning that the rate reflects recent history, not the current HVAC state. Added an IMPORTANT instruction in the decision section: if HVAC is currently idle, `hold`/`wait` means zones drift toward ambient — only use them when zones are already at or past target.

## [1.0.1] - 2026-02-25

### Fixed
- **Thermostat overshooting target stops heat prematurely**: When the thermostat location was warmer than the schedule target (e.g. hallway thermostat at 71°F with a 69°F target), `compute_adjusted_setpoint` anchored the setpoint to `desired_temp_c` (69°F) + zone error (1°F) = 70°F — below the thermostat's own reading of 71°F. The thermostat considered itself satisfied and stopped heating, leaving zones stuck at 67.8°F. The formula now detects this case: when the thermostat has already passed the schedule target in the active HVAC direction (heat: thermostat > desired; cool: thermostat < desired), it uses the thermostat's current reading as the anchor so the adjusted setpoint stays ahead of where the thermostat currently is and keeps the HVAC running. In the example above: base = 71°F + zone error 1°F = 72°F → heat continues until zones reach 69°F. Total offset from the schedule target is still capped at `max_temp_offset_f` (default 8°F).

## [1.0.0] - 2026-02-25

### Added
- **LLM-driven predictive climate advisor**: The LLM is now the primary decision-maker for thermostat setpoint adjustments. On each maintenance tick, `ClimateAdvisor` assembles rich context — zone averages, thermostat reading, temperature trend data from TimescaleDB 5-min and hourly continuous aggregates, the zone's learned thermal profile, current occupancy, outdoor weather, and recent device actions — and asks the configured LLM whether to `adjust`, `hold`, or `wait`. `SafetyProtocol` vetoes only physically dangerous values (below 55°F / above 90°F) and max-offset violations; routine suppression decisions are left entirely to the LLM.
- **Zone thermal analytics background task** (`zone_analytics.py`): Runs every 4 hours via apscheduler. Queries 30 days of `sensor_readings` and `device_actions` per zone and computes heating/cooling rates, HVAC response lag, typical overshoot, per-hour occupancy scores, sleep pattern detection (lux ≤ 10 lx + presence = sleeping), and midday nap detection. Results are persisted to `zones.thermal_profile` (JSONB) and consumed by the LLM advisor on every decision tick.
- **1°F dead-band in offset compensation**: Rooms that are within 1°F of target in the correct direction (≤1°F above target in heat mode, ≤1°F below in cool mode) skip the advisor and return the schedule target unchanged, eliminating micro-corrections for thermostat rounding noise.
- **AI toggle in Settings → Logic tab**: A new "AI Decision Making" card with a toggle switch lets users enable or disable the LLM advisor at runtime. When disabled, the formula-based offset result is used directly; when the LLM is unavailable or returns an unparseable response, the formula is used as an automatic fallback with a WARNING log.
- **Advisor cache invalidation on drift**: `_handle_climate_state_change` (the drift-correction handler) now calls `clear_advisor_cache()` in addition to clearing `_last_offset_temp`, ensuring the LLM produces a fresh decision immediately after a thermostat drift event.

### Changed
- **Default LLM**: Anthropic `claude-sonnet-4-6` is now the primary provider across `decision_engine.py` and `climate_advisor.py`; OpenAI `gpt-4o-mini` is the secondary fallback (was reversed).
- **`apply_offset_compensation` return expanded to 5-tuple**: Returns `(adjusted_c, offset_c, zone_names, avg_temp_c, hvac_mode)` so callers can pass zone context to the advisor without redundant HA fetches.
- **Dead clamp removed**: The conditional heat/cool clamp introduced in v0.8.41 and partially relaxed in v0.9.5 is removed. Clamping is now only performed by `SafetyProtocol` for absolute physical bounds.

## [0.9.5] - 2026-02-25

### Fixed
- **Offset clamp now allows setpoint to bypass schedule target when zones are overshot**: Previously the heat-mode clamp unconditionally floored the adjusted setpoint at the schedule target — even when zone temperatures were already *above* the target. This meant that if rooms overheated (e.g. 76°F when the schedule is 68°F), the formula correctly computed a below-target setpoint (e.g. 60°F) to suppress heating, but the clamp overrode it back to 68°F. The HVAC would then run or remain on because the thermostat setpoint equalled the schedule target. The clamp now only applies when zone temperatures are on the *correct* side of the target: in heat mode the floor is enforced only when `avg_zone_temp < desired` (normal heating scenario); when zones are already above the target the negative offset is allowed through so the thermostat setpoint drops below the schedule target and the HVAC stops heating until the rooms cool. The same conditional logic applies to cool mode (ceiling only enforced when `avg_zone_temp > desired`).

## [0.9.4] - 2026-02-25

### Fixed
- **Impossible temperature warning for Ecobee climate entities**: The WebSocket state parser was comparing `unit == "°F"` (exact match) when deciding whether to convert temperature attribute values from Fahrenheit to Celsius. Ecobee climate entities report `temperature_unit` as `"F"` (no degree symbol), so the exact match failed, and the raw Fahrenheit value (e.g. 69°F) was stored as 69°C — triggering a `WARNING - Dropping impossible temperature` log on every thermostat state change. Changed the check to `"F" in unit.upper()` (matching the pattern used in `_parse_temp_from_state`) so both `"°F"` and `"F"` are handled correctly.

## [0.9.3] - 2026-02-25

### Fixed
- **Drift correction blocked by maintenance loop skip guard**: When the Ecobee's own schedule (e.g. away mode) changed the thermostat setpoint away from what ClimateIQ last set, the drift detector correctly triggered `maintain_climate_offset()` — but the maintenance loop's "no update needed" check compared the new adjusted setpoint against its own cached last-sent value (which was already correct). Since `adjusted == prev`, it skipped sending the correction and the thermostat stayed at the drifted value. `_handle_climate_state_change` now clears `_last_offset_temp` before triggering the correction, ensuring the maintenance loop always re-sends when drift is detected.

## [0.9.2] - 2026-02-24

### Fixed
- **DB fallback stale sensor data causing over-heating**: `_get_db_zone_temp_c` now only uses sensor readings from the last 30 minutes. Previously, when the HA live sensor was unavailable the fallback could return readings from hours earlier (when the room was cold), producing a spurious positive offset that set the thermostat above the schedule target and caused the HVAC to heat rooms that were already well above the target temperature.

## [0.9.1] - 2026-02-24

### Fixed
- **Chat page crash when AI uses tools**: `ChatMessage.actions` was typed as OpenAI tool-call format (`{ function: { name } }`) but the backend sends action results shaped as `{ tool, args, ... }`. Accessing `action.function.name` on a non-empty `actions_taken` list threw a `TypeError` that crashed the React chat UI. Updated `ChatAction` type to match the backend shape and render `action.tool` instead.

## [0.9.0] - 2026-02-24

### Security
- **Prompt injection hardening** (CRITICAL): LLM-extracted user directives are now capped at 200 characters and XML-escaped before being inserted into the system prompt. The directive block is wrapped in `<user_directives>` tags with an explicit data-only comment so the model treats it as preferences rather than instructions.
- **Backup file size limit** (CRITICAL): `POST /backup/import` now rejects files larger than 50 MB with a 413 response before parsing JSON, preventing unbounded memory allocation.
- **Generic LLM error messages** (HIGH): Raw exception details (which can contain API key fragments and internal URLs) are no longer returned to chat clients. Errors are logged server-side and a generic retry message is shown to the user.
- **WebSocket authentication** (HIGH): `/ws` and `/ws/zones` now check the `api_key` query parameter when an API key is configured, closing unauthenticated connections with code 4001 before accepting them.
- **Backup ID validated as UUID** (HIGH): `DELETE /backup/{backup_id}` now accepts only valid UUID values — FastAPI rejects non-UUID inputs with 422 before the handler runs.
- **X-Request-ID sanitization** (HIGH): Client-supplied `X-Request-ID` headers are validated against a safe alphanumeric pattern before being echoed in responses and logs. Malformed values are replaced with a fresh UUID.
- **API keys removed from config property** (HIGH): `Settings.llm_provider_config` no longer includes `api_key` fields, preventing accidental serialisation of credentials if the property is ever returned from a route.
- **Standalone mode authentication warning** (MEDIUM): Starting the server in non-add-on mode without an `api_key` configured now logs a prominent `WARNING` explaining all endpoints are publicly accessible.
- **HA service call payloads downgraded to DEBUG** (MEDIUM): `ha_client.call_service` previously logged full entity ID + temperature payloads at INFO level. Downgraded to DEBUG to reduce operational data in production logs.

### Fixed
- **None guard in offset compensation**: `apply_offset_compensation` now returns immediately if `desired_temp_c` is `None` rather than crashing with a `TypeError` deep in the formula.
- **Zone delete FK conflict** (MEDIUM): `DELETE /zones/{id}` now returns HTTP 409 Conflict with an actionable message instead of a 500 Internal Server Error when the zone is still referenced by foreign-key constrained rows.
- **Redis fallback connection leak**: The per-request Redis client created in the `get_redis` fallback path is now properly closed in a `finally` block, preventing connection pool exhaustion when the shared client is not initialised.

### Dependencies (security updates)
- `aiohttp>=3.11` → `>=3.13.3` — patches 5 CVEs including path traversal (CVE-2025-69226), DoS (CVE-2025-69228), zip bomb (CVE-2025-69223), and HTTP request smuggling (CVE-2025-53643).
- `litellm>=1.42` → `>=1.66` — clears CVE-2025-45809 (SQL injection) and CVE-2025-0330 (Langfuse API key leak).
- `google-generativeai` → `google-genai>=1.0` — Google terminated support for `google-generativeai` on 2025-11-30; migrated to the current GA SDK.
- `openai>=1.58` → `>=2.0` — aligns with the current major SDK version; v1 receives only maintenance updates.
- `fastapi>=0.111` → `>=0.115`, `uvicorn>=0.30` → `>=0.34`, `alembic>=1.13` → `>=1.15` — floor bumps for quality and bug-fix coverage.
- `ruff>=0.5` → `>=0.15` (dev) — formatter style guide changed significantly at 0.15.

## [0.8.42] - 2026-02-24

### Fixed
- **Zone temperature always resolves for avg/offset**: `get_avg_zone_temp_c` and `get_priority_zone_temp_c` previously only read from HA live sensors. If a sensor entity was unavailable or hadn't been polled yet, the zone was excluded from the average — causing `schedule_avg_temp` to return null and the dashboard to fall back to the all-zones average. Both functions now fall back to the most-recent DB sensor reading (same source as the zone cards), ensuring the schedule-zone avg and offset compensation always have data when sensor readings exist in the database.

## [0.8.41] - 2026-02-24

### Fixed
- **Offset compensation clamp**: When a zone is already above the schedule target in heat mode, the offset formula was computing a setpoint *below* the schedule target (e.g. 66°F when the target is 69°F and Oliver's room reads 72°F). This caused the thermostat to sit too low — the HVAC would only restart once the thermostat location dropped below that under-target setpoint, risking under-heating. The adjusted setpoint is now floored at the schedule target in heat/heat_cool mode and ceilinged at the target in cool mode. The thermostat correctly holds at 69°F and does not heat (room temp already exceeds setpoint), then resumes heating naturally once the room cools below target.

## [0.8.40] - 2026-02-24

### Fixed
- **Dashboard Avg Temp card**: Was showing the average across all zones regardless of which schedule is active. Now prefers `schedule_avg_temp` (the avg of only the schedule's targeted zones) when a schedule is active. For example, when "Oliver's Bed" activates with only Oliver's room targeted, the card now shows Oliver's room temp instead of the whole-house average.
- **Zone card Target temp**: Was showing the thermostat's hardware setpoint (offset-adjusted) as the "Target" label on every zone card. Now shows the active schedule's desired temperature for zones that are part of the current schedule — the value the system is actually trying to achieve in that room.

## [0.8.39] - 2026-02-24

### Fixed
- **Chat zone temperature fallback**: The HA live-sensor fallback in `get_zone_context` was calling `float(state.state)` on any non-unavailable sensor entity without checking `device_class`. Zigbee multisensors expose multiple entities (lux, battery%, humidity, etc.) — these numeric values were silently treated as °C zone temperatures, causing the LLM to report impossible readings like 93.2°F or 134.6°F. The fallback now only accepts entities with `device_class == "temperature"` or `unit_of_measurement` of `°F`/`°C` with no device_class (matching the pattern used elsewhere for multisensors).

## [0.8.38] - 2026-02-24

### Fixed
- **Chat LLM hallucination**: The AI assistant was inventing temperature readings (e.g. 96°F) for zones that had no sensor data (shown as "awaiting sensor data" in context). Added explicit grounding rules to the system prompt: the LLM must never invent, estimate, or infer sensor values not explicitly present in the context data, and must tell the user when data is unavailable rather than fabricating it.


## [0.8.37] - 2026-02-24

### Fixed
- **Thermostat drift detection**: Temperature unit was read from entity attributes (often absent on Ecobee climate entities), defaulting to °C and misinterpreting 64°F setpoints as 64°C — causing massive false drift and constant correction loops. Now uses `settings_instance.temperature_unit` (the system setting) for correct unit conversion.
- **Hold preservation**: `ecobee.resume_program` was called with `resume_all=True`, which cancelled all holds including ClimateIQ's own temperature hold. Changed to `resume_all=False` to cancel only the current comfort-preset hold, preventing Ecobee from bouncing back to its away schedule (64°F) between resume and the subsequent set_temperature call.

## 0.8.36

### Fixed

- **Immediate thermostat drift correction**: ClimateIQ now reacts in real time
  when the thermostat setpoint is changed externally (e.g. Ecobee away mode
  resetting the heat setpoint to 64°F). A new `_handle_climate_state_change`
  callback is registered on the existing HA WebSocket and fires within 3 seconds
  of any `state_changed` event on the climate entity. If the thermostat's
  current heat setpoint differs from what ClimateIQ last set by more than 1°F
  (one Ecobee step), `maintain_climate_offset` is triggered immediately rather
  than waiting up to 60 seconds for the next scheduled tick. Rapid thermostat
  transitions are debounced so only one correction runs at a time.

## 0.8.35

### Fixed

- **Thermostat set to wrong temperature in heat_cool/auto mode**: In dual-setpoint
  mode (Ecobee "auto"), the old code treated the adjusted setpoint as the
  *midpoint* of the heat/cool spread and subtracted half the spread to get the
  heat setpoint — e.g. target 69°F with a 10°F spread → heat setpoint 64°F.
  The fix: the adjusted setpoint is the heating target and is sent directly as
  `target_temp_low`. The existing cooling setpoint (`target_temp_high`) is kept
  unchanged (only raised if it would fall within 2°F of the heat setpoint to
  satisfy Ecobee's minimum spread requirement).

## 0.8.34

### Fixed

- **Zones disappearing after v0.8.33**: The batched `UNION ALL` raw SQL query
  introduced in v0.8.33 used `ANY(:ids)` with a Python list, which psycopg3
  does not auto-cast to a PostgreSQL array — causing a DB error that made the
  entire `/zones` endpoint return a 500. Reverted to the original 4-query
  approach but now uses `asyncio.gather` to run all four queries concurrently
  rather than sequentially, preserving the performance benefit without the
  type-binding issue.

## 0.8.33

### Performance

- **DB migration 002**: Added five missing indexes — `sensor_readings(sensor_id, recorded_at DESC)`
  compound covering index (the most queried pattern); `sensors(zone_id)` and `devices(zone_id)`
  FK indexes; partial indexes on `zones(is_active)` and `schedules(is_enabled)`.  The
  sensor_readings compound index eliminates full-table scans on the largest table.

- **Zone enrichment batched**: Replaced the 4-queries-per-zone loop in
  `_enrich_zone_response` with a single UNION ALL query that fetches the latest
  non-null value for temperature, humidity, presence, and lux in one round trip.
  Zone list requests now issue O(1) DB reads instead of O(zones × 4).

- **Background task staggering**: All scheduled tasks now have staggered
  `start_date` offsets (0–55 seconds) so they no longer fire simultaneously.
  `execute_schedules` and `maintain_climate_offset` previously both hit the DB
  at exactly T+60s; they now start at T+10s and T+20s respectively.

- **Frontend prefetch on app init**: `AppProviders` now kicks off
  `prefetchQuery` for `settings`, `override-status`, and a zones warm-up
  request the moment the app boots — before the router renders any page.
  React Query deduplicates the in-flight requests so the Dashboard never
  waits for a cold fetch.

## 0.8.32

### Fixed

- **Offset applied in whole-degree increments (°F).** Ecobee thermostats
  move their setpoint in 1°F steps and round at 0.5°F, so a fractional
  offset (e.g. +0.3°F) has no effect — the thermostat sees the same
  setpoint and doesn't turn the heat back on. The zone error is now
  rounded to the nearest whole °F before being applied, guaranteeing
  every non-zero offset actually crosses the thermostat's rounding
  threshold.

## 0.8.31

### Fixed

- **Offset compensation now drives toward zone target instead of tracking
  the thermostat-to-zone gap.** The old formula used
  `desired + (thermostat - zone_avg)` which only compensated for the
  current sensor location gap and would stop heating once the thermostat
  *read* its setpoint — regardless of whether the zones were actually warm
  enough. The new formula uses `desired + (desired - zone_avg)`: the
  thermostat is pushed *above* the target by however much the zones are
  *below* it, so the HVAC keeps running until the zones reach the desired
  temperature. Once zones hit target the offset is zero; if they overshoot,
  the offset goes negative, preventing runaway. The 8°F max-offset cap and
  60-second maintenance loop are unchanged.

- **Status bar now shows thermostat set temp** alongside the reading:
  "Thermostat: 72°F → 73°F" makes the offset-adjusted hardware setpoint
  visible at a glance.

## 0.8.30

### Fixed

- **Target Temp, Current Temp, and All Zones no longer show `--`**. Three
  compounding bugs caused these values to always be null:

  1. **Scope bug** — `_best` (active schedule) was declared inside an inner
     try-block but referenced outside it, causing a silent `NameError` that
     nulled all three values on every request.

  2. **Exception silenced at DEBUG** — failures in the offset computation
     block were logged at DEBUG (invisible in default logs). Now logged at
     WARNING and output variables are pre-declared so a partial failure
     doesn't zero out unrelated fields.

  3. **Sensor reading too strict** — `_get_live_zone_temp_c` rejected sensors
     whose HA entity has a numeric temperature state but no `device_class` or
     `unit_of_measurement`. Now tries three strategies: explicit temp entity,
     plausible numeric state, and temperature stored as an attribute. Also
     checks the sensor's secondary `entity_id` field as a fallback.

  4. **Duplicate schedule-lookup logic** replaced with the shared
     `_get_user_tz` / `parse_time` helpers from the schedule route.

## 0.8.29

### Fixed

- **"Avg Temp" card now shows schedule target temp**, not the thermostat's
  offset-adjusted setpoint. The "Set:" label reflects what ClimateIQ wants
  the rooms to be, not what it sends to the Ecobee hardware.

- **"Current Temp" and "All Zones" status bar values no longer show `--`**.
  Zone sensors (Zigbee multisensors, etc.) that lack a `device_class` attribute
  in HA but have a temperature unit of measurement are now accepted when
  computing live zone averages.

## 0.8.28

### Fixed

- **Target Temperature now shows the schedule's desired temp** instead
  of the thermostat's offset-adjusted setpoint.  The big number in the
  Manual Override card is what ClimateIQ wants the rooms to be, not
  what the thermostat is told to target.

- **Targeting line now shows only active schedule zones** instead of
  the priority zone from offset calculation.  Previously it could show
  zones (e.g. Master Bedroom, Oliver's Room) that weren't part of the
  active schedule.

- **Faster Dashboard polling** -- zone data refreshes every 15s
  (was 30s), override status every 10s (was 15s), active schedule
  every 30s (was 60s) for a more realtime feel.

## 0.8.27

### Fixed

- **Offset compensation now reads live sensor data from Home Assistant**
  instead of querying the sensor_readings database table with a
  30-minute cutoff.  Previously, sensors that hadn't reported a state
  change (because the temperature was stable) would be excluded from
  the zone average, causing the offset to be calculated from only a
  subset of schedule zones.  Now all zone sensors are queried live
  from HA -- only sensors marked unavailable/unknown are skipped.

## 0.8.26

### Fixed

- **Suppressed third-party DEBUG logs at INFO level** -- websockets
  and uvicorn loggers were dumping verbose DEBUG output (connection
  headers, ping/pong, WebSocket frames) even when log level was set
  to `info`.  These loggers are now set to WARNING when not in debug
  mode.

- **Removed preset_mode calls from set_temperature_with_hold** --
  Ecobee automatically creates a temperature hold when
  `set_temperature` is called.  The explicit `set_preset_mode` calls
  with `temp`/`hold` were unnecessary and caused 500 errors.
  `set_temperature_with_hold` now just calls `set_temperature`.

## 0.8.25

### Fixed

- **Offset compensation now uses average of ALL schedule zones** --
  previously used only the highest-priority zone for the offset
  calculation, which could pick a single zone (e.g. Office at 69.8 F)
  that was close to the thermostat, resulting in a tiny offset.  Now
  averages all zones in the active schedule (e.g. Dining Room 68 F,
  Living Room 66 F, Office 70 F, Kitchen 68 F, Foyer 67 F = avg 67.8 F)
  so the offset reflects the true gap between the thermostat and the
  rooms being heated.

- **Silenced Ecobee hold preset errors** -- `set_temperature_with_hold`
  was logging ERROR/WARNING for 500 responses when trying to set
  `temp`/`hold` presets on Ecobee.  Ecobee automatically creates a
  temperature hold on `set_temperature` -- the explicit preset calls
  are unnecessary.  Downgraded to DEBUG level.

### Improved

- **Climate offset maintenance logging at INFO level** -- all key
  decision points now log at INFO level so they appear in the default
  add-on logs.

## 0.8.22

### Added

- **Schedule and all-zones average temperatures on Dashboard** -- the
  Manual Override status bar now shows three temperature readings:
  "Thermostat" (Ecobee hallway sensor), "Current Temp" (average of
  zones in the active schedule), and "All Zones" (average across
  every active zone).  Backend returns `schedule_avg_temp` and
  `all_zones_avg_temp` from the override status endpoint.

- **`get_avg_zone_temp_c()` helper** -- new function in
  `temp_compensation.py` that averages temperatures across all
  matching zones regardless of priority (unlike the existing
  priority-based function used for offset calculation).

## 0.8.21

### Improved

- **Balance temperature across same-priority zones** -- when multiple
  zones in the active schedule share the same highest priority,
  their temperatures are now averaged for offset compensation instead
  of arbitrarily picking one.  For example, if a nighttime schedule
  targets two bedrooms at priority 5, the offset is calculated from
  the average of both rooms so the system heats to balance between
  them.  The Dashboard shows both zone names (e.g. "Targeting Master
  Bedroom, Guest Bedroom").

## 0.8.20

### Fixed

- **Offset compensation now scoped to active schedule's zones** -- the
  Dashboard override status and the climate maintenance loop were
  picking the highest-priority zone globally (e.g. Master Bedroom)
  even when that zone was not part of the currently-active schedule.
  Now both `get_override_status()` and `maintain_climate_offset()` find
  the active schedule and only consider its assigned zones for offset
  compensation.

## 0.8.19

### Added

- **Dedicated climate offset maintenance loop** -- new
  `maintain_climate_offset()` background task runs every 60 seconds,
  independent of schedule firing.  Finds the currently-active schedule,
  re-evaluates offset compensation using live sensor and thermostat
  readings, and updates the thermostat whenever the adjusted setpoint
  drifts by more than 0.5 C.  Skips Follow-Me and Active modes (they
  handle offset in their own loops).  Replaces the v0.8.18
  schedule-window-bound re-eval with a proper continuous control loop.

## 0.8.18

### Fixed

- **Continuous offset re-evaluation for active schedules** -- offset
  compensation was only applied at the moment a schedule fired (within
  a 2-minute window of start_time).  After that, the thermostat held a
  stale setpoint even as zone and hallway temperatures drifted.  Now,
  while a schedule is active (between start_time and end_time), the
  offset is re-evaluated every 60 seconds and the thermostat is updated
  whenever the adjusted setpoint changes by more than 0.5 C.

## 0.8.17

### Fixed

- **Database migration for zones.priority column** -- v0.8.16 added a
  `priority` column to the Zone ORM model but did not include the
  corresponding `ALTER TABLE` migration, causing every query that
  touches the `zones` table to fail with
  `UndefinedColumn: column zones.priority does not exist`.  The startup
  migration now adds the column automatically.

## 0.8.16

### Added

- **Temperature offset compensation** -- ClimateIQ now adjusts the
  target temperature sent to the thermostat to compensate for the
  difference between the thermostat's built-in sensor (e.g. hallway)
  and the priority zone's actual temperature (from Zigbee sensors).
  If the hallway reads 73 F but the bedroom reads 66 F and you want
  69 F in the bedroom, ClimateIQ tells the thermostat to target 76 F.
  Integrated into schedule execution, Follow-Me mode, and Resume
  Schedule.

- **Zone priority (1-10)** -- each zone now has a configurable
  priority.  The highest-priority zone with a recent sensor reading
  is used for offset compensation.  Default is 5.  Editable in the
  Zones page.

- **Max temperature offset setting** -- configurable in Settings
  (default 8 F / 4.4 C).  Caps how much ClimateIQ will adjust the
  thermostat target.  Set to 0 to disable offset compensation.

- **Offset info on Dashboard** -- the Manual Override card now shows
  which zone ClimateIQ is targeting and the current offset when
  compensation is active.

## 0.8.15

### Fixed

- **Stop clearing Ecobee "temp" preset on set_temperature** -- the
  `temp` preset means a temperature hold is already active, which is
  exactly what `set_temperature` creates.  Clearing it via
  `resume_program` snapped back to the Ecobee schedule (e.g. sleep at
  68) and then the subsequent `set_temperature` re-created the hold,
  causing a visible flip-flop between presets.  Now only
  comfort-profile presets (sleep, away, home) are cleared.

- **Override status no longer shows "Override Active" for normal
  temperature holds** -- the `temp` preset is normal ClimateIQ
  operation (we set a temp, Ecobee shows it as a hold).  Only
  comfort-profile presets (sleep, away, home) now trigger the
  "Override Active" badge in the UI.

## 0.8.14

### Fixed

- **Clear thermostat presets before setting temperature** -- when the
  thermostat has an active preset (sleep, away, home, etc.) that holds
  it to a comfort profile, `set_temperature` calls are rejected with
  400. ClimateIQ now detects active presets and clears them
  transparently before sending the temperature command. Tries
  `ecobee.resume_program` first, falls back to setting preset to
  "none".

## 0.8.13

### Changed

- **Enhanced error logging for HA service calls** -- 400/4xx errors
  now log the full response body from HA (up to 500 chars) so we can
  see the exact rejection reason. ``call_service`` also logs the
  complete JSON payload being sent. This will reveal why
  ``set_temperature`` is being rejected.

## 0.8.12

### Fixed

- **set_temperature 400 error -- wrong service parameters** -- the HA
  ``climate.set_temperature`` service uses ``temperature`` for
  single-setpoint modes (heat, cool) and ``target_temp_low`` +
  ``target_temp_high`` only for dual-setpoint modes (heat_cool, auto).
  The v0.8.5 fix incorrectly sent ``target_temp_low`` for heat mode
  and ``target_temp_high`` for cool mode, which HA rejects with 400.
  Now uses ``{"temperature": 69.0}`` for heat/cool/off and only
  switches to the low/high pair for heat_cool/auto. Simplified the
  method to default to ``temperature`` and only override for
  dual-setpoint modes.

## 0.8.11

### Fixed

- **Timezone lookup was importing nonexistent function** --
  ``_get_user_tz()`` tried ``from backend.integrations import
  get_ha_client`` which doesn't exist (``get_ha_client`` is in
  ``backend.api.dependencies``). This ``ImportError`` was silently
  caught, causing the HA config timezone fallback to never execute,
  so the system always fell back to UTC. Fixed to import
  ``_ha_client`` from ``backend.api.dependencies`` directly. This
  was the root cause of all schedule time display issues -- schedule
  times were being treated as UTC instead of the user's local
  timezone.

- **Active schedule still appearing in upcoming list** -- the dedup
  filter only removed the first occurrence of the active schedule
  from the upcoming list, but a second occurrence (next day) remained.
  Now filters out ALL occurrences of the active schedule ID.

## 0.8.10

### Fixed

- **Timezone resolution falling back to UTC** -- ``_get_user_tz()``
  had a fallback path that tried ``Settings.timezone`` which doesn't
  exist, causing a silent ``AttributeError`` that fell through to UTC.
  If the ``system_settings`` DB table has no timezone row, the system
  was treating all schedule times as UTC, shifting them by the user's
  offset (e.g., an 8:00 AM schedule showing as 3:00 AM in EST).
  Now falls back to the HA config ``time_zone`` field (from
  ``/api/config``) before defaulting to UTC. Added debug logging to
  trace which source the timezone came from. Same fix applied to
  ``execute_schedules()`` in ``main.py``.

- **Active schedule shown twice on dashboard** -- the active schedule
  badge and the upcoming schedules list both showed the same schedule.
  The upcoming list now filters out the first occurrence of the
  currently active schedule so it only appears in the green "Now
  Active" badge. Also fixed React duplicate key warnings by using
  index-based keys for schedule occurrences.

## 0.8.9

### Fixed

- **All HA service calls: entity_id was nested under "target"** --
  ``HAClient.call_service()`` was sending
  ``{"target": {"entity_id": "..."}, ...}`` but the HA REST API
  expects ``entity_id`` at the top level of the JSON body. The
  ``target`` nesting is a WebSocket API convention, not REST. This
  caused 400 Bad Request errors on ``climate.set_temperature`` and
  other service calls. Fixed by flattening the target dict into the
  top-level payload. Added diagnostic logging to ``set_temperature``
  showing the exact payload and detected HVAC mode.

## 0.8.8

### Changed

- **Resume Schedule re-applies ClimateIQ schedule** -- the "Resume
  Schedule" button now finds the currently active ClimateIQ schedule
  and re-applies its target temperature to the thermostat, instead of
  trying to resume the Ecobee's own program. ClimateIQ is the control
  system; the thermostat is just an actuator. If no ClimateIQ schedule
  is currently active, the button reports that there is nothing to
  resume. All thermostat-specific hold management (Ecobee vacation
  holds, preset modes) remains an internal implementation detail of
  the temperature-setting methods.

## 0.8.7

### Fixed

- **Upcoming schedule times wrong on dashboard** -- the upcoming
  schedules endpoint mixed UTC and local time when computing
  occurrences, causing wrong times and duplicate entries. Rewrote
  the logic to work entirely in local time: walks each calendar day
  in the window, checks if the schedule fires on that weekday, builds
  local datetimes from the schedule's HH:MM strings, then converts to
  UTC only at the end for the API response. End times are also built
  in local time before conversion, fixing the midnight-wrap case.

- **Resume Schedule button not working** -- the resume quick action
  silently returned success even when all three fallback methods
  failed. Now properly logs each attempt, tries
  ``ecobee.resume_program``, always attempts to delete the
  ``ClimateIQ_Control`` vacation hold, and falls back to setting the
  ``home`` preset. Returns ``success: false`` with details if all
  methods fail. Frontend also now awaits the async action before
  refetching override status.

### Removed

- **Set Temp stat card** -- removed the compact thermostat set-point
  card from the stats grid since the full Manual Override card below
  already provides the same functionality with more control.

## 0.8.6

### Fixed

- **Resume Schedule quick action 400 error** -- the "Resume Schedule"
  button sent ``set_preset_mode`` with ``"none"`` which Ecobee rejects.
  Now tries ``ecobee.resume_program`` first (cancels all holds and
  restores the Ecobee's own schedule), then falls back to the generic
  preset clear for non-Ecobee thermostats, and finally tries deleting
  the ``ClimateIQ_Control`` vacation hold as a last resort.

## 0.8.5

### Fixed

- **Manual override / set_temperature 400 error** -- Ecobee thermostats
  in ``heat`` mode reject the generic ``temperature`` parameter in the
  HA ``climate.set_temperature`` service call, requiring
  ``target_temp_low`` instead (and ``target_temp_high`` for cool mode).
  ``HAClient.set_temperature()`` now reads the entity's current HVAC
  mode and sends the correct parameter: ``target_temp_low`` for heat,
  ``target_temp_high`` for cool, both for auto/heat_cool, or the
  generic ``temperature`` for other modes. This fixes the 400 Bad
  Request errors on manual override, schedule execution, follow-me,
  and active mode temperature changes.

- **Schedule execution crash** -- ``execute_schedules()`` referenced
  ``settings_instance.timezone`` which does not exist on the
  ``Settings`` class, causing an ``AttributeError`` on every schedule
  tick. Fixed to default to UTC and let the DB ``system_settings``
  timezone value take precedence (which was already the next step in
  the code).

## 0.8.4

### Fixed

- **Schedule timezone handling** -- schedule times are stored as local
  HH:MM strings but `execute_schedules()` was comparing them against
  UTC time, causing schedules to fire at the wrong hour. Both the
  schedule executor in `main.py` and `get_next_occurrence()` in
  `schedule.py` now resolve the user's timezone from the
  `system_settings` table and work in local time.

- **Impossible temperature readings in chat/LLM context** -- the live
  HA fallback paths in `chat.py` (`get_zone_context` and
  `get_conditions_context`) had no validation, so a sensor reporting
  Fahrenheit without proper `unit_of_measurement` could pass through
  as raw Celsius (e.g., 68 degrees F stored as 68 degrees C). Added
  `_validate_temp_c()` helper that returns `None` for temps outside
  -40 to 60 degrees C. Applied to all DB read and HA fallback paths.
  Dashboard also validates zone temperatures before averaging.

- **Schedule time display** -- schedules page showed raw 24-hour
  format ("14:00") instead of 12-hour format ("2:00 PM"). Added
  `formatTime12h()` helper. Dashboard upcoming schedules also use
  `hour12: true` in `toLocaleTimeString`.

### Added

- **Active schedule indicator** -- new `GET /api/v1/schedules/active`
  endpoint that determines which schedule is currently running based
  on the user's timezone, day of week, and time window. Returns the
  highest-priority active schedule. The dashboard displays a green
  "Now Active" badge with a pulsing dot above the upcoming schedules
  list, showing the schedule name, target temperature, zones, and
  end time.

- **Compact thermostat set-temp card** -- new stat card in the
  dashboard stats grid (next to "Occupied") showing the current
  thermostat set-point with inline +/- buttons for quick adjustments.
  Uses a purple icon to differentiate from the orange average temp
  card. Clicking +/- immediately sends the override to the backend.

## 0.8.3

### Added

- **Ecobee schedule override** -- when ClimateIQ enters scheduled,
  active, or follow-me mode it now creates an Ecobee "vacation" hold
  (`ClimateIQ_Control`) to prevent the thermostat's internal schedule
  from reverting setpoints. Smart Home/Away and Follow Me are disabled
  so ClimateIQ has sole occupancy control. Switching back to learn mode
  deletes the hold and restores Ecobee's normal program.

- **Manual temperature override** -- new `POST /api/v1/system/override`
  endpoint for direct thermostat control with Ecobee hold management,
  plus `GET /api/v1/system/override` for current thermostat state.

- **Dashboard override UI** -- prominent manual override card on the
  dashboard with large +/- buttons, range slider, "Set Override" and
  "Resume Schedule" controls, and live thermostat status display.

- **ClimateIQ Lovelace card** -- HACS-compatible custom Lovelace card
  (`climateiq-card`) with dark glassmorphism theme. Displays current
  thermostat state, zone summary, quick actions, and manual override.
  Communicates with the add-on via HA ingress. Separate repo at
  `climateIQ-lovelace-card/`.

### Changed

- `ha_client.set_temperature_with_hold()` replaces plain
  `set_temperature()` in all mode executors (schedules, follow-me,
  active) to maintain Ecobee vacation holds automatically.

## 0.8.2

### Fixed

- **Chat history crash** -- fixed `ConversationHistoryItem` Pydantic
  validation error where the SQLAlchemy `metadata` descriptor (from
  `DeclarativeBase`) was returned instead of the JSONB column value.
  Applied the same fix to `ConversationResponse` and
  `UserFeedbackResponse` schemas.

- **Chat zone status accuracy** -- the LLM now falls back to live Home
  Assistant sensor states when DB readings are missing, preventing
  incorrect "offline" reports for zones that are actually online.
  Zone context explicitly labels zones as ONLINE with sensor counts.

### Added

- **Chat memory system** -- conversations are now mined for long-term
  user preferences and directives (e.g. "never heat the basement above
  65 F", "I prefer it cooler at night"). Extracted directives are stored
  in a new `user_directives` table and injected into both the chat
  system prompt and the Active-mode AI decision loop so the system
  remembers user preferences across sessions.

- **Directive management API** -- `GET /api/v1/chat/directives` to list
  active directives, `POST` to create manually, `DELETE` to deactivate.

- **Memory sidebar in Chat UI** -- the conversation sidebar now shows a
  "Memory" section listing all active directives with the ability to
  remove them.

## 0.8.1

### Changed

- **Dark glassmorphism UI redesign** — complete visual overhaul of the
  frontend with a dark glassmorphism aesthetic inspired by Humidity
  Intelligence V2. In dark mode, cards use translucent backgrounds with
  backdrop-blur, colored glow shadows, and gradient accents. Light mode
  uses clean solid backgrounds with subtle shadows.

- **Design system foundation** — new CSS custom properties for glass
  backgrounds, borders, and glow effects. Utility classes `.glass-card`
  and `.glow-border-*` for consistent glassmorphism across components.
  State-driven color tokens (safe, cool, warning, danger, purple).

- **Updated UI components** — Card, Button, Input, and ThemeToggle
  components updated with dark-mode translucent backgrounds, gradient
  buttons with glow, and glass input fields.

- **Layout shell redesign** — sidebar with dark glass background and
  glowing active nav items, header with lane-button mode switcher,
  ambient radial gradient overlays on the main container.

- **Dashboard redesign** — hero stat cards with glowing icon circles,
  instrument-panel typography (font-black), glass weather widget, glass
  schedule items, and glass temperature override popup.

- **ZoneCard redesign** — status-driven colored left border and glow
  shadow (green for occupied, sky for idle), pulse animation on occupied
  status dot, horizontal chip/pill layout for metrics.

- **Analytics redesign** — glass chart containers with updated tooltip
  styling, glass stat cards, glass tab navigation and time range
  selectors.

- **All pages updated** — Zones, Schedules, Chat, and Settings pages
  all updated with consistent glassmorphism treatment, refined
  typography (font-black for values, 10px bold uppercase labels), and
  dark-mode glass borders/backgrounds.

## 0.8.0

### Added

- **Zone metrics exclusion** — zones can now be excluded from analytics
  aggregates (overview, comfort scores, energy estimates) and from the AI
  control loop (RuleEngine comfort enforcement, PID vent optimization,
  PatternEngine learning). Useful for zones like basements that are
  intentionally kept at different temperatures and would skew whole-house
  metrics.

- **Month-based exclusion schedule** — exclusion can be limited to specific
  calendar months (e.g. Nov-Mar for a basement that's only excluded in
  winter). When no months are selected, the exclusion applies year-round.

- **Exclusion UI in zone settings** — new "Metrics & Control Exclusion"
  card on the zone detail page with a toggle, month selector buttons, and
  a status badge showing whether the zone is currently excluded or active.

- **`is_currently_excluded` computed field** on zone API responses — tells
  the frontend whether the zone is excluded right now based on the current
  month and the configured exclusion settings.

## 0.7.9

### Fixed

- **Single zone selection shows empty graph in Analytics** — selecting a
  single zone in the Temperature or Occupancy tabs showed "No data available"
  even when data existed. The single-zone path used a separate `/history`
  endpoint that picked a different (less-populated) TimescaleDB aggregate
  view than the multi-zone overview endpoint. Unified all zone selections
  (single, multi, all) to use the `/overview` endpoint consistently.

- **Humidity not showing in Analytics** — the Temperature tab's Humidity
  metric toggle showed an empty chart. The continuous aggregate views group
  by `(sensor_id, zone_id, bucket)`, so zones with separate temperature and
  humidity sensors produced multiple rows per time bucket. The backend
  returned these as separate readings, and the frontend overwrote real values
  with null from the other sensor's row. Fixed by re-aggregating across
  sensors in the overview SQL query (`GROUP BY zone_id, bucket`) and adding
  a frontend guard that never overwrites a non-null value with null.

## 0.7.8

### Added

- **Multi-zone selection in Analytics** — the zone selector on Temperature,
  Occupancy, and Energy tabs now supports selecting multiple specific zones
  (toggle buttons) in addition to "All Zones" or a single zone. Previously
  only "All Zones" or one zone at a time was possible.

- **`zone_ids` query parameter** on `/analytics/overview`, `/analytics/energy`,
  and `/analytics/comfort` endpoints — accepts a comma-separated list of zone
  UUIDs to filter results to a subset of zones.

- **Array parameter support in `buildUrl`** — the frontend API helper now
  supports `ParamValue[]` types, joining array values as comma-separated
  strings for query parameters.

## 0.7.7

### Added

- **ZoneManager wired into production** — the ZoneManager singleton is now
  initialized at startup, hydrated from the database with all active zones,
  and fed real-time sensor data from the WebSocket stream. Zone states are
  maintained with EMA-smoothed sensor values and live comfort scoring.

- **RuleEngine background task** — runs every 2 minutes to enforce comfort
  band limits, humidity control, occupancy-based setback adjustments, and
  anomaly detection across all zones.

- **PID Controller vent optimization** — per-zone PID controllers run every
  3 minutes, computing smart vent positions (10–100% open) based on the
  difference between current and target temperatures, with anti-windup and
  autotuning support.

- **PatternEngine learning** — occupancy and thermal pattern learning runs
  every 30 minutes, building per-zone models of typical occupancy schedules
  and thermal response characteristics for preconditioning.

- **Schedule preconditioning** — when a schedule is approaching, the pattern
  engine's thermal model is used to start heating/cooling early so the zone
  reaches the target temperature by the scheduled start time.

- **Schedule zone verification** — after a schedule fires, the system
  monitors zone sensors and sends a notification alert if any zone is more
  than 1.5°C off its target temperature after 15 minutes.

### Fixed

- **Cover automation crash** — `execute_cover_automation()` referenced
  `app_state.ha_client` which doesn't exist on the AppState dataclass.
  Fixed to use `_deps._ha_client`.

## 0.7.6

### Fixed

- **Hypertable primary key incompatibility** — TimescaleDB requires the
  partitioning column (`recorded_at`) to be part of all unique constraints.
  The `sensor_readings` and `device_actions` tables had UUID-only primary
  keys, causing `create_hypertable` to fail. Fixed by dropping the UUID-only
  PK and adding a composite primary key `(id, recorded_at)`.

- **Continuous aggregates fail inside transactions** — `CREATE MATERIALIZED
  VIEW ... WITH (timescaledb.continuous)` cannot run inside a transaction
  block. Previously these ran inside `engine.begin()` and silently failed.
  Now continuous aggregates are created on a separate AUTOCOMMIT connection.

## 0.7.5

### Fixed

- **Database init cascade failure** — `_ensure_timescaledb_objects()` ran all
  DDL inside a single transaction. When the first `create_hypertable` call
  failed, PostgreSQL put the transaction into `InFailedSqlTransaction` state,
  causing ALL subsequent DDL (continuous aggregates, policies) to silently
  fail. Restructured to use SAVEPOINTs so each DDL statement is isolated —
  a failure in one does not abort the rest.

### Changed

- **Dynamic version banner** — `run.sh` now reads the version from
  `/app/VERSION` at runtime instead of having it hardcoded. One fewer file
  to update on version bumps.

## 0.7.4

### Fixed

- **Sensor health check verifies with HA before alerting** — `check_sensor_health`
  previously relied solely on the `Sensor.last_seen` database timestamp to
  declare sensors offline. Now it pings the Home Assistant REST API
  (`get_state()`) to verify the entity is actually unavailable before sending
  a false offline notification.

- **Comfort scores raw fallback** — the `/analytics/comfort` endpoint now
  falls back to raw `sensor_readings` data when TimescaleDB continuous
  aggregate views don't exist, instead of returning empty results.

## 0.7.3

### Fixed

- **Analytics MissingGreenlet crash** — `get_overview()` called `db.rollback()`
  in except blocks, which expired Zone ORM objects. Subsequent access to
  `zone.id` / `zone.name` triggered synchronous lazy-loading inside an async
  context, raising `MissingGreenlet`. Fixed by eagerly capturing zone info
  (`[(z.id, z.name) for z in zones]`) before any rollback can occur.

- **Dashboard humidity display** — `_enrich_zone_response` queried the last
  50 `SensorReading` rows and picked the first match per field. Humidity
  readings were pushed out of the 50-row window by more frequent temperature
  updates. Replaced with 4 targeted queries (one per field: temperature,
  humidity, lux, occupancy), each fetching only the latest row.

- **Raw fallback for overview endpoint** — when TimescaleDB aggregate views
  are missing, the overview endpoint now falls back to querying raw
  `sensor_readings` instead of returning empty time series.

## 0.7.2

### Fixed

- **Sensor offline false alerts** — `check_sensor_health` was sending
  offline notifications based on stale `last_seen` timestamps even when
  sensors were actively reporting. Improved the health check logic to
  reduce false positives.

- **Analytics zero-data** — multiple analytics endpoints returned empty
  results due to query issues with the continuous aggregate views. Fixed
  query logic to properly handle missing or empty aggregate data.

- **Dashboard zone navigation** — clicking a zone card on the Dashboard
  now navigates to the zone detail view. Previously zone cards were not
  clickable.

## 0.7.1

### Added

- **Lux-driven cover automation** — new background task that monitors
  illuminance sensors and automatically adjusts cover/blind positions
  based on configurable lux thresholds. Closes covers when lux exceeds
  the high threshold (reduce solar heat gain) and opens them when below
  the low threshold.

- **Occupancy inference** — zones without dedicated occupancy sensors can
  now infer occupancy from motion sensor activity patterns and door
  sensor state changes.

### Improved

- **Dashboard enhancements** — zone cards show set temperature alongside
  current temperature, improved visual hierarchy and status indicators.

## 0.7.0

### Added

- **Lux display in zone detail view** — zones with illuminance sensors
  now show the current lux reading on a dedicated card in the zone
  detail page.

- **Occupancy display in zone detail view** — zones with occupancy or
  motion sensors now show the current occupancy state in the zone detail
  page.

### Fixed

- **Dashboard humidity not showing** — humidity values were missing from
  zone cards due to the sensor reading query window being too narrow.
  Initial fix applied here, with a more thorough fix in v0.7.3.

## 0.6.8

### Fixed

- **HA device registry uses WebSocket API** — replaced broken REST API calls
  (`/api/config/device_registry` and `/api/config/entity_registry` return 404)
  with HA WebSocket commands (`config/device_registry/list` and
  `config/entity_registry/list`). Added `send_command()` method to
  `HAWebSocketClient` for request/response WS commands.

- **TimescaleDB continuous aggregates created at startup** — `init_db()` now
  creates hypertables and the `sensor_readings_5min`, `sensor_readings_hourly`,
  and `sensor_readings_daily` continuous aggregate views if they don't exist,
  along with refresh and compression policies.

- **Analytics endpoints gracefully handle missing views** — `get_zone_history`,
  `get_overview`, and `get_comfort_scores` now catch `ProgrammingError` when
  aggregate views are missing and fall back to raw data or empty results
  instead of crashing with a 500 error.

## 0.6.7

### Added

- **HA device picker in Zones** — new "Add Device" button lets you select
  a Home Assistant device (e.g., a multi-sensor) and import all its
  sensor/binary_sensor entities at once with checkboxes. Uses the HA device
  and entity registry APIs to group entities by physical device.

- **`GET /settings/ha/devices` endpoint** — returns HA devices with their
  grouped sensor/binary_sensor entities (name, manufacturer, model, area).

- **`POST /sensors/bulk` endpoint** — creates multiple sensors at once from
  a device selection, with automatic deduplication and WS filter registration.

- **HA device/entity registry support** — `HAClient` now has
  `get_device_registry()` and `get_entity_registry()` methods for querying
  the HA REST API device/entity registries.

### Fixed

- **Schedule overlap check with legacy `zone_id`** — `check_schedule_overlap()`
  now falls back to the legacy `zone_id` field when `zone_ids` is empty,
  correctly detecting that different zones don't overlap.

- **`ScheduleCreate` now forbids extra fields** — changed from `extra="ignore"`
  to `extra="forbid"` so unknown fields are rejected with a 422 validation error.

## 0.6.6

### Fixed

- **Sensor data not showing in zones** — the HA WebSocket entity filter
  now includes all registered sensors' `ha_entity_id` values from the DB,
  not just entities listed in the config/settings. Previously, assigning a
  sensor to a zone would show the entity but state_changed events were
  silently dropped because the entity wasn't in the WebSocket filter.

- **Value extraction from HA entities** — `_parse_state_change()` now uses
  `device_class` and `unit_of_measurement` attributes (the standard HA way)
  to extract temperature, humidity, illuminance, and occupancy. Previously
  it only matched keywords in entity IDs (e.g., "temperature" had to appear
  in the entity ID string). Also converts Fahrenheit to Celsius automatically
  when `unit_of_measurement` is `°F`.

- **Entity filter reads from DB settings** — the WebSocket entity filter
  now reads `climate_entities` and `sensor_entities` from the
  `system_settings` KV table (set via the Settings UI), not just config.

### Added

- **Dynamic entity filter updates** — when a new sensor with an
  `ha_entity_id` is created, it is immediately added to the running
  WebSocket entity filter (no restart needed).

- **Searchable entity picker** — the sensor assignment form now has a
  search/filter input instead of a bare dropdown. Search by entity name,
  entity ID, or device class. Can also paste a custom entity ID directly.
  Shows entity state, device class, and unit of measurement in results.

- **Enhanced entity info** — the `GET /settings/ha/entities` endpoint now
  returns `domain`, `device_class`, and `unit_of_measurement` fields.

## 0.6.5

### Added

- **Whole-house analytics overview** — the Temperature and Occupancy tabs
  now default to an "All Zones" view showing every zone on a single chart.
  Temperature tab renders a multi-line chart (one colored line per zone)
  with a Temperature/Humidity metric toggle. Occupancy tab shows a grouped
  bar chart with per-zone occupancy rates by hour.

- **`GET /analytics/overview` backend endpoint** — new endpoint that queries
  TimescaleDB continuous aggregates for all active zones in a single query,
  returning per-zone time series with temperature, humidity, and occupancy.
  Automatically selects the optimal aggregate view (5min/hourly/daily) based
  on the lookback window.

### Improved

- **Extended color palette** — 16 distinct HSL colors for zone
  differentiation (up from 8) to support all 11 zones.

- **Zone selector** on Temperature and Occupancy tabs now includes an
  "All Zones" button (selected by default) alongside individual zone
  buttons for drill-down.

## 0.6.4

### Improved

- **Renamed "ClimateIQ Copilot" to "ClimateIQ Advisor"** in the chat UI.

- **AI chat now has full live system context** — the LLM system prompt
  now includes the current system mode, thermostat state (HVAC mode,
  current/target temp, preset, fan mode), all system settings, every
  enabled schedule with zone names and timing, and current weather data.
  The AI can now accurately answer "what mode is the system in?",
  "what's the thermostat set to?", "what schedules are active?", etc.

## 0.6.3

### Improved

- **Multi-zone schedule selection** — schedules now support selecting
  multiple specific zones (e.g., "all bedrooms") instead of only one zone
  or all zones. The zone picker uses toggle chips similar to the day-of-week
  selector, with "All zones" and "Select all" shortcuts.

- **Priority explanation** — the priority slider in the schedule form now
  includes helper text: 1-3 for defaults, 4-7 for regular schedules, 8-10
  for overrides. Higher priority schedules take precedence when overlapping.

### Changed

- **Schedule data model** — `zone_id` (single UUID) replaced by `zone_ids`
  (JSONB array of UUIDs). Empty array = all zones. The old `zone_id` column
  is preserved for backwards compatibility. A migration in `init_db()` auto-
  converts existing schedules on startup.

- **API schema** — `zone_id`/`zone_name` fields replaced by `zone_ids`/
  `zone_names` arrays on all schedule endpoints. Conflict detection updated
  to handle set-based zone overlap.

## 0.6.2

### Improved

- **Analytics now use TimescaleDB continuous aggregates** — the history
  and comfort endpoints no longer fetch all raw sensor readings into
  Python for aggregation. Instead, queries automatically select the best
  pre-computed view (5-min, hourly, or daily) based on the lookback
  window and requested resolution. For a 30-day query, this reduces the
  row count from ~86,400 per sensor to ~720 (hourly buckets). The comfort
  endpoint also replaced its N+1 per-zone query pattern with a single
  bulk SQL query grouped by zone_id.

### Fixed

- **LLM model list now populates** — the Settings > LLM Providers tab
  was always showing 0 models because the backend endpoints were stubs
  returning hardcoded empty lists. Now the listing and refresh endpoints
  call the existing `discover_models()` module which makes real API calls
  to each provider (Anthropic, OpenAI, Gemini, Grok, Ollama, LlamaCPP).
  Discovery runs in a background thread with a 5-second timeout to avoid
  blocking, and results are cached for 5 minutes. The response now
  includes model display names and context lengths.

### Added

- **Comprehensive diagnostics endpoint** `GET /system/diagnostics` —
  checks 11 system components in a single request: database connectivity,
  TimescaleDB extensions/hypertables/continuous aggregates, table row
  counts, Redis PING + SET/GET, Home Assistant REST + WebSocket,
  background scheduler job status, notification service, and LLM provider
  configuration. Returns structured results with per-component status,
  latency measurements, and an overall health assessment.

## 0.6.1

### Added

- **Logic Reference system** — new `GET /system/logic-reference` endpoint
  returns structured documentation of how ClimateIQ works (10 sections:
  architecture, modes, schedules, zones, thermostat, notifications, energy,
  weather, chat, data storage).

- **Settings > Logic tab** — new tab in Settings displays the full logic
  reference as styled cards. Users can read how every feature works
  without leaving the UI.

- **AI chat now has full system context** — the LLM system prompt now
  includes a condensed version of the logic reference, so the AI
  assistant can accurately explain how Follow-Me mode, schedules,
  Active/AI mode, and all other features work when asked.

## 0.6.0

### Added

- **Schedule management page** — new `/schedules` route with full CRUD
  UI. Create, edit, delete, enable/disable schedules. Day-of-week pills,
  time pickers, zone selector, target temp in user's unit, HVAC mode,
  priority slider. Conflict warnings displayed at top. Added to sidebar
  navigation with CalendarClock icon.

- **Schedule execution engine** — new background task (every 60s) that
  evaluates enabled schedules against the current time and fires them
  by calling `set_temperature` on the global thermostat. Uses a 2-minute
  match window with dedup to prevent re-firing. Converts C→F when HA
  is in Imperial. Records actions in `DeviceAction` table.

- **Follow-Me mode** — new background task (every 90s) that activates
  when system mode is `follow_me`. Reads occupancy from per-zone sensor
  readings, adjusts the global thermostat to the occupied zone's comfort
  preference temp. Multiple occupied zones get averaged. No occupancy
  falls back to eco temp (18°C/64°F). Only fires if target changes by
  more than 0.5°C.

- **Active/AI mode** — new background task (every 5m) that activates
  when system mode is `active`. Gathers all zone data, weather, current
  thermostat state, today's schedules, and comfort preferences. Asks
  the LLM to recommend an optimal temperature with reasoning. Applies
  safety clamps and only changes if diff > 0.5°C.

- **HA mobile app notifications** — `NotificationService` singleton
  initialized at startup, wired into schedule execution (confirms
  activations), sensor health checks (offline alerts), follow-me mode
  changes, and AI mode decisions. Uses `notification_target` setting
  from `system_settings` KV table (e.g., `mobile_app_joshua_s_iphone`).

### Removed

- Debug endpoint `GET /zones/debug/thermostat` (temporary, no longer
  needed).

## 0.5.4

### Added

- **Quick actions now work** — new `POST /system/quick-action` endpoint
  that controls the global thermostat directly via HA climate services.
  Actions: `eco` (set preset or lower by 3°), `away` (set Away preset),
  `boost_heat` (+2°), `boost_cool` (-2°), `resume` (clear preset).
  Previously quick actions went through the chat command parser which
  couldn't match the text patterns and had no access to the global
  thermostat (only per-zone devices which don't exist).

## 0.5.3

### Fixed

- **32°F / 0% no longer shown for zones without sensors** — the backend
  returned `0` (not `null`) for `current_temp` and `current_humidity`
  when no sensor data exists. Frontend now treats `0` as no-data and
  shows "--" instead of 32°F/0%.

## 0.5.2

### Fixed

- **Zones show "--" when no sensor is assigned** — temperature,
  humidity, status, and occupancy all show "--" instead of fake
  defaults (0%, Clear, 0°) when a zone has no sensor data. Only the
  target setpoint (shared from the global thermostat) shows a value.
  Per-zone values will appear once Zigbee sensors are assigned.

## 0.5.1

### Fixed

- **Zone current temp no longer shows thermostat reading** — the global
  climate fallback was setting `current_temp` on every zone from the
  Ecobee's own sensor, making all rooms show the same temperature.
  Now only the target setpoint is shared from the global thermostat.
  Current temp will show "--" until per-zone Zigbee sensors are assigned.

## 0.5.0

### Changed

- **Whole-house thermostat support** — zone enrichment no longer
  requires a thermostat device to be manually linked to each zone.
  When no per-zone thermostat device exists, the backend reads the
  global `climate_entities` setting (from DB or add-on config) and
  fetches live current temp + target setpoint from HA. Every zone
  gets the Ecobee's target temp; per-zone sensors (when installed)
  will override the current temp. The global climate state is cached
  for 15 seconds to avoid hitting HA once per zone.

## 0.4.9

### Added

- **Debug endpoint** `GET /api/v1/zones/debug/thermostat` — dumps raw
  HA thermostat entity state, attributes, DB capabilities, and HA unit
  system. Hit this to see exactly what HA is returning so we can fix
  the target temp mapping.

## 0.4.8

### Fixed

- **Ecobee target temp now correct** — Ecobee thermostats use
  `target_temp_low` (heat), `target_temp_high` (cool), or both (auto)
  instead of the generic `temperature` attribute. The backend now reads
  the HVAC mode and picks the correct setpoint. Previously `target_temp`
  came back null and the frontend showed the 22°C default (71.6°F).
- **Build fix** — missing closing braces in Dashboard onClick handlers,
  and `unitKey` declared after its use in `handleTempOverride`.

## 0.4.7

### Fixed

- **Thermostat temperatures now correct when HA uses Imperial (°F)** —
  the backend was storing raw HA temperature values without converting
  to Celsius. Since HA returns temps in the user's configured unit
  system, an HA instance set to Imperial would send 71°F which the
  backend stored as 71, and the frontend then converted "71°C" to
  159.8°F. Now the backend detects HA's unit system via `GET /api/config`
  (cached after first call) and converts F→C before storing. The
  frontend's C→display-unit conversion then produces correct values.

- **Dashboard temperature override respects user's unit** — the
  up/down temp override widget was hardcoded to a 10–35 range (Celsius)
  and sent raw values to the backend. Now uses the user's display unit
  (50–95°F or 10–35°C), seeds with the target temp converted to the
  display unit, and converts back to Celsius before sending.

## 0.4.6

### Fixed

- **Settings now persist to database** — `GET /settings` and
  `PUT /settings` were non-functional stubs that returned hardcoded
  defaults and silently discarded all writes. Rewrote the entire
  `settings.py` backend to properly read/write the `system_settings`
  KV table. All user preferences (timezone, temperature unit, comfort
  ranges, energy cost, currency, entity selections) now persist across
  add-on restarts.
- **Entity discovery endpoint** — `GET /settings/ha/entities?domain=...`
  now uses the initialized HA REST client to return real entities.
- **LLM provider listing** — `GET /settings/llm/providers` returns
  actual provider status based on configured environment variables.

## 0.4.5

### Fixed

- **Live thermostat data now actually works** — the HA REST client
  (`_ha_client`) was never initialized at startup. It was only created
  lazily when a route used `Depends(get_ha_client)`, but the zones
  endpoint reads the module-level variable directly. Since no route
  triggered the lazy init before zones were fetched, `_ha_client` was
  always `None` and the live HA thermostat block was silently skipped.
  Now the REST client is initialized during app startup in `lifespan()`,
  so `GET /zones` correctly returns the thermostat's live
  `current_temperature` and `temperature` (setpoint) attributes.

## 0.4.4

### Fixed

- **Temperature unit respect throughout UI** — all temperature displays
  now honor the user's chosen unit (°C or °F) from Settings. Previously
  every page hardcoded °C. Affected locations:
  - **Settings** — comfort temp min/max labels, input values, and
    live-converting when toggling the unit selector. Values convert back
    to Celsius before saving to the backend.
  - **Dashboard** — average temperature stat card, upcoming schedule
    target temps.
  - **Zones** — list view temperature stat, detail view avg/range stats,
    24-hour history chart data + legend, comfort preference labels/values
    and save-back conversion.
  - **Analytics** — temperature history chart data + legend, avg/min/max
    stat cards, comfort zone averages.
- Added `toDisplayTemp`, `toStorageCelsius`, and `tempUnitLabel` utility
  helpers in `lib/utils.ts`.

## 0.4.3

### Fixed

- **Sidebar fully opaque on mobile** — the sidebar used `bg-card/70`
  (70% opacity) with `backdrop-blur`, making text blurry and hard to read
  on phones where the main content bled through. Now uses solid `bg-card`
  on mobile and only applies the translucent glass effect on desktop
  (`lg:bg-card/70 lg:backdrop-blur`) where the sidebar is static.

## 0.4.2

### Fixed

- **Sidebar closes on navigation** — tapping a nav link on mobile now
  closes the sidebar automatically instead of leaving it covering the
  content. Added explicit `left-0` to the fixed sidebar for reliable
  positioning in HA ingress webviews. Overlay z-index lowered so sidebar
  links remain clickable.
- **Live thermostat data now shown** — zone enrichment now **prefers**
  live Home Assistant thermostat data (current_temperature, target
  temperature) over stale database readings. Previously, any existing DB
  sensor reading (even zeroes from init) would take priority and the live
  HA data was silently skipped.

## 0.4.1

### Fixed

- **Mobile responsiveness overhaul** — the entire UI is now usable on
  phones and the Home Assistant mobile app. No design changes; purely
  responsive adjustments:
  - Main sidebar defaults to closed on screens < 1024px instead of
    covering 77% of the viewport on load.
  - Chat conversation sidebar defaults to closed on mobile and uses a
    slide-over overlay (like the main sidebar) instead of stealing inline
    width.
  - Header mode-switcher buttons wrap and use smaller padding on narrow
    screens so all four modes remain accessible.
  - Analytics time-range selector stacks below the page title on mobile
    instead of overflowing off-screen.
  - Analytics tab labels hidden on mobile (icon-only), matching the
    Settings tab pattern.
  - ZoneCard stats grid switches from a fixed 3-column layout to
    single-column on mobile.
  - Zone detail and Analytics summary stats use `sm:grid-cols-2
    lg:grid-cols-4` instead of jumping from 1 to 4 columns at 640px.
  - Layout and Card padding reduced on mobile (`p-3`/`p-4` base,
    `sm:p-6` on larger screens).
  - Input font size set to 16px on mobile (`text-base sm:text-sm`) to
    prevent iOS Safari auto-zoom on focus.
  - Dashboard temperature override button is now visible on touch devices
    (was hidden behind hover-only opacity).
  - Temperature override controls enlarged from 24px to 32px for better
    touch targets.
  - Sidebar close and hamburger buttons enlarged for easier tapping.
  - Entity names in Settings truncated with hidden entity_id on mobile to
    prevent row overflow.
  - Entity filter search input uses full width on mobile.
  - `overflow-x: hidden` added to body to prevent horizontal scroll from
    any stray overflow.
  - Sensor form HA entity picker moved inside its parent grid so
    `sm:col-span-2` works correctly.
  - Chat "Send" label and "New Chat" label hidden on mobile (icon only).

## 0.4.0

### Added

- **Live thermostat data on Dashboard** — zone cards now show real-time
  current temperature and target temperature fetched directly from Home
  Assistant climate entities instead of relying on stale DB readings.
- **Energy entity integration** — new `energy_entity` add-on option lets
  users point to a real HA energy sensor (e.g., a utility meter). Energy
  card on the Dashboard reads live state from HA and only appears when an
  entity is configured — no more fabricated heuristic estimates.
- `GET /api/v1/analytics/energy/live` endpoint returning live energy
  reading from the configured HA entity.
- **HA entity picker for sensors** — the sensor creation form in the Zones
  page now shows a dropdown of available HA sensor entities. Selecting one
  auto-fills the sensor name and links the `ha_entity_id`.
- **Energy entity picker in Settings** — Settings > Home Assistant tab
  includes a new picker for selecting the energy monitoring entity.

### Changed

- `_enrich_zone_response` in the zones API now accepts an optional HA
  client and fetches live thermostat state (`current_temperature`,
  `temperature`) for devices that have an `ha_entity_id`.
- Dashboard energy card replaced: uses live HA data via
  `/analytics/energy/live` instead of the heuristic `/analytics/energy`
  endpoint.

### Removed

- Tuning/Settings button from the header (redundant with sidebar
  navigation).
- Heuristic energy trend indicators (`TrendingUp`/`TrendingDown`) from
  the Dashboard stats bar.

## 0.3.1

### Added

- **Entity discovery UI** — Settings > Home Assistant tab now shows
  interactive multi-select lists for climate and sensor entities, populated
  live from Home Assistant. Users can search, select, and save entity
  filters directly from the web UI instead of editing add-on YAML.
- `GET /api/v1/settings/ha/entities` endpoint with optional `domain`
  query parameter for discovering available HA entities.
- `climate_entities` and `sensor_entities` are now persisted in the
  database settings table so they survive add-on restarts when set via UI.

### Fixed

- **Layout gap in HA ingress** — removed a redundant spacer `div` in the
  Layout component that doubled the sidebar width, causing ~2 inches of
  blank space to the right of the navigation column.

## 0.3.0

### Added

- **Entity filtering** — choose which Home Assistant entities ClimateIQ
  monitors instead of subscribing to all state changes.
  - `climate_entities`: list of `climate.*` entity IDs to track.
  - `sensor_entities`: list of `sensor.*` / `binary_sensor.*` entity IDs to track.
  - `weather_entity`: single `weather.*` entity for forecast polling.
  - When lists are empty (the default), all entities in the supported domains
    are accepted (previous behavior).
- Seed `weather_entity` into the database `system_settings` table on startup
  when configured via add-on options, so the weather poller picks it up
  automatically.

## 0.2.11

### Fixed

- Make `CREATE EXTENSION` calls non-fatal during `init_db()` so startup
  succeeds when the DB user isn't a superuser (extensions must be
  pre-installed by an admin).

## 0.2.10

### Fixed

- URL-encode database username and password with `quote_plus` so special
  characters (like `@`) in passwords don't break the connection URL parsing.
  This was the root cause of the "Name does not resolve" errors.

## 0.2.9

### Changed

- Replace `asyncpg` with `psycopg` (psycopg3) as the async PostgreSQL driver.

## 0.2.7

### Fixed

- Pre-resolve DB hostname to an IP address before handing the URL to asyncpg so
  that `getaddrinfo` is never called inside the asyncio thread-pool (broken on
  Alpine musl). Resolution happens both in `run.sh` (shell-level) and in
  `database.py` (`_pre_resolve_url`) as a defense-in-depth measure.

## 0.2.6

### Fixed

- Force uvicorn to run on the built-in `asyncio` event loop across the add-on,
  Docker image, and local development setups to avoid uvloop DNS resolution
  failures on Alpine/musl.

### Changed

- Document asyncio loop requirement across README and DOCS so non-HA deployments
  mirror the Home Assistant runtime behavior.

## 0.2.0

### Removed

- MQTT support removed entirely (all sensor data comes via Home Assistant WebSocket)
- Embedded PostgreSQL and Redis removed from add-on (external services required)
- Nginx ingress proxy removed (uvicorn serves directly on ingress port)

### Added

- Configuration field descriptions in HA add-on UI (translations/en.yaml)

### Fixed

- Add-on config save error when ollama_url is empty (changed schema from url? to str?)

## 0.1.0 - Initial Release

### Added

- Home Assistant add-on with ingress support
- Lightweight container (FastAPI/Uvicorn serves API + frontend SPA directly)
- Requires external TimescaleDB and Redis
- Multi-zone HVAC management dashboard
- Real-time sensor monitoring via Home Assistant WebSocket
- AI-powered chat interface with multi-provider LLM support
  - Anthropic Claude
  - OpenAI GPT
  - Google Gemini
  - xAI Grok
  - Ollama (local inference)
- Smart scheduling with time-based temperature profiles
- Weather integration for proactive climate adjustments
- Energy usage analytics
- Support for amd64 and aarch64 architectures
