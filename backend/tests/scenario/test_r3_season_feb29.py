"""Default seasons: Winter ends Feb 28, Spring starts Mar 1 → Feb 29 (leap
years) belongs to no season, so the winter heat lock silently lifts."""
from datetime import date

from backend.core.seasonal_lock import DEFAULT_SEASONS, Season, find_active_season


def test_default_seasons_cover_feb_29() -> None:
    seasons = [Season(**s) for s in DEFAULT_SEASONS]
    s = find_active_season(seasons, date(2028, 2, 29))
    assert s is not None and s.name == "Winter", f"Feb 29 → {s}"
