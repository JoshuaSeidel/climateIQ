"""§3.3 local time everywhere: Zone.is_currently_excluded uses the UTC month,
so a zone excluded for Jun-Aug (a cold basement) re-joins the average at
20:00 EDT on Aug 31 (= Sep 1 UTC) and drags the house's control."""
from backend.tests.scenario.harness import House


async def test_exclude_months_uses_local_month(house: House) -> None:
    await house.add_zone("Living Room", 76)
    await house.add_zone("Basement", 60, exclude_from_metrics=True, exclude_months=[6, 7, 8])
    await house.add_schedule("Evening", "17:00", "23:00", 72)  # all zones
    house.ha.set_thermostat(mode="cool", setpoint_f=74, reading_f=75)

    house.at(2026, 8, 31, 19, 0)  # 23:00 UTC, still Aug both ways
    await house.tick_scheduled()
    assert house.ha.would_cool(), "precondition: house at 76F cools toward 72F"

    house.at(2026, 8, 31, 21, 0)  # local Aug 31 21:00 = Sep 1 01:00 UTC
    await house.tick_scheduled()
    assert house.ha.would_cool(), (
        f"cool stopped (setpoint {house.ha.setpoint_f}F, mode {house.ha.mode}): the Aug-excluded "
        "basement re-joined the average at 20:00 local because the month check is UTC"
    )
