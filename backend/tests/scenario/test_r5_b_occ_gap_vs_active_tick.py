"""Probe §6b.3 / §6.2: the occupancy dwell restarts if observations are >5 min apart
(_OCCUPANCY_OBSERVE_GAP_S == 300), but Active mode only observes every 5 min (300 s +
scheduler jitter), so the dwell restarts on ~half of ticks and a real change is delayed
or (with a steady +0.5 s lag) never honoured."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import time_machine

import backend.api.main as main_mod


def test_active_mode_five_minute_ticks_still_converge() -> None:
    main_mod._zone_occupancy_stable.clear()
    t0 = datetime(2026, 11, 9, 17, 0, tzinfo=UTC)
    zid = "z-bed"
    with time_machine.travel(t0, tick=False):
        assert main_mod._stable_occupancy(zid, False) is False  # established vacant
    # Room becomes truly occupied and stays so; Active ticks every 5 min (+0.5 s jitter).
    seen = []
    for i in range(1, 9):  # 40 minutes
        with time_machine.travel(t0 + timedelta(seconds=i * 300.5), tick=False):
            seen.append(main_mod._stable_occupancy(zid, True))
    assert seen[-1] is True and True in seen[:3], (
        f"room occupied continuously for 40 min (3 min dwell) but stable={seen}"
    )
