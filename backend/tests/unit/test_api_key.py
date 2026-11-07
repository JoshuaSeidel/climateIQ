"""Unit tests for API key generation, persistence and rotation."""

from __future__ import annotations

from pathlib import Path

import pytest

import backend.core.api_key as api_key


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLIMATEIQ_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(api_key, "_cached_key", None)
    monkeypatch.setattr(api_key, "_env_key", lambda: "")


def test_generated_key_is_persisted_and_reused(tmp_path: Path) -> None:
    key = api_key.get_api_key()
    assert len(key) >= 40
    assert (tmp_path / "api_key").read_text() == key
    api_key._cached_key = None  # simulate restart
    assert api_key.get_api_key() == key


def test_rotate_replaces_key(tmp_path: Path) -> None:
    old = api_key.get_api_key()
    new = api_key.rotate_api_key()
    assert new != old
    assert api_key.key_matches(new)
    assert not api_key.key_matches(old)


def test_env_key_wins_and_cannot_rotate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_key, "_env_key", lambda: "fixed-key")
    assert api_key.get_api_key() == "fixed-key"
    with pytest.raises(RuntimeError):
        api_key.rotate_api_key()


def test_empty_candidate_never_matches() -> None:
    api_key.get_api_key()
    assert not api_key.key_matches("")
    assert not api_key.key_matches(None)
