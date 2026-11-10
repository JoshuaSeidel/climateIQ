from typing import Any

import backend.api.main as main_mod
from backend.models.enums import SystemMode
from backend.tests.scenario.harness import House
from backend.tests.scenario.r4_helpers import _use_llm

MON = (2026, 11, 9)


async def test_unknown_occ_prompt(house: House, monkeypatch: Any) -> None:
    llm = _use_llm(monkeypatch, "RECOMMENDED_TEMP: 70\nREASON: ok")
    prompts = []
    orig = llm.generate

    async def gen(**kw):
        prompts.append(kw["user_prompt"])
        return await orig(**kw)

    llm.generate = gen  # type: ignore
    await house.add_zone("Den", 71.0, comfort_preferences={"temp_min": 20, "temp_max": 23})
    await house.set_mode(SystemMode.active)
    house.ha.set_thermostat(mode="heat", setpoint_f=70, reading_f=70)
    # Den was stably occupied (e.g. seen by Follow-Me / earlier tick), now no occupancy signal at all
    house.at(*MON, 10, 0)
    zid = str(house.zones["Den"])
    main_mod._stable_occupancy(zid, True)

    async def none_occ(z, db):
        return None

    monkeypatch.setattr(main_mod, "infer_zone_occupancy", none_occ)
    house.at(*MON, 10, 5)
    await main_mod.execute_active_mode()
    print(prompts[-1] if prompts else "NO PROMPT")
    assert prompts and "(vac)" not in prompts[-1], (
        "unknown occupancy rendered as vac although stable state was occupied"
    )
