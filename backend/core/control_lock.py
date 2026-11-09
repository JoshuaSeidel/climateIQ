"""Coordination between everything that writes to the thermostat (spec §2).

Several writers can run at once: the scheduled loops (maintenance, Active,
Follow-Me), the drift correction, Resume, manual overrides from the API /
dial / chat, mode switches and emergency shutoff.  Without coordination a
loop that had already passed its permission check could write *after* an
emergency shutoff or a new manual override.

``THERMOSTAT_LOCK`` serialises the short "re-check permission → read state
→ write" critical section of every writer.  Slow work (LLM calls, sensor
reads for the decision) happens outside it.

``single_flight`` keeps one instance of a loop running at a time even when
it is triggered from several places (scheduler, drift correction, Resume).
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

THERMOSTAT_LOCK = asyncio.Lock()

_F = TypeVar("_F", bound=Callable[..., Awaitable[Any]])


def single_flight(func: _F) -> _F:  # noqa: UP047
    """Skip a call while another call of the same loop is still running."""
    lock = asyncio.Lock()

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if lock.locked():
            return None
        async with lock:
            return await func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
