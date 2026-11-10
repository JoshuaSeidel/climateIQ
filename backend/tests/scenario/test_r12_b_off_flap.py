"""Sensors die DURING an off schedule: ClimateIQ's own off is undone and re-applied (flap + notification each time)."""
import backend.api.main as main_mod
from backend.integrations.ha_client import EntityState
from backend.tests.scenario.harness import House

MON = (2026, 11, 9)

class N:
    def __init__(self): self.sent = []
    async def send_ha_notification(self, **kw): self.sent.append(kw)

async def test_off_schedule_sensor_loss_flap(house: House) -> None:
    await house.add_zone("Living Room", 70.0)
    await house.add_schedule("Day", "06:30", "22:00", 72, ["Living Room"])
    await house.add_schedule("Away", "09:00", "11:00", 60, [], hvac_mode="off", priority=9)
    house.ha.set_thermostat(mode="heat", setpoint_f=72, reading_f=70)
    n = N()
    main_mod._notification_service = n  # type: ignore[assignment]
    modes = []
    t = 8 * 60 + 50
    while t <= 10 * 60 + 30:
        house.at(*MON, t // 60, t % 60)
        if t >= 9 * 60 + 30:
            house.ha.entities["sensor.living_room_temperature"] = EntityState(
                entity_id="sensor.living_room_temperature", state="unavailable", attributes={})
        await house.tick_scheduled()
        modes.append(house.ha.mode)
        t += 1
    after = modes[40:]
    print("MODES", [k["message"][:50] for k in n.sent][:5], len(n.sent), "|", "".join(m[0] for m in after), "notifs:", len(n.sent))
    assert "heat_cool" not in after and "auto" not in after, f"off schedule fought: {after[:10]} notifs={len(n.sent)}"
