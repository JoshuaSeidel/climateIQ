"""API key for direct (non-ingress) access to the ClimateIQ API.

In add-on mode the API listens on the host network, so anything on the LAN
can reach it.  Requests proxied by Home Assistant ingress are authenticated
by HA; every other request (the ESP32 dial, scripts, curl) must present this
key as ``Authorization: Bearer <key>`` (or ``?api_key=<key>`` for WebSocket
clients that cannot set headers).

Key source, in order:
1. ``CLIMATEIQ_API_KEY`` (env / settings) — fixed by the operator.
2. ``<data dir>/api_key`` — generated on first start and persisted, so it
   survives restarts and add-on updates.  Viewable and rotatable from
   Settings → API Access (reachable through HA ingress).
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

_cached_key: str | None = None


def _data_dir() -> Path:
    return Path(os.environ.get("CLIMATEIQ_DATA_DIR") or Path(__file__).resolve().parents[2] / "data")


def _key_file() -> Path:
    return _data_dir() / "api_key"


def _env_key() -> str:
    from backend.config import SETTINGS

    return str(SETTINGS.api_key or "").strip()


def is_key_from_env() -> bool:
    return bool(_env_key())


def get_api_key() -> str:
    """Return the active API key, generating and persisting one if needed."""
    global _cached_key
    if _cached_key:
        return _cached_key
    env_key = _env_key()
    if env_key:
        _cached_key = env_key
        return _cached_key
    path = _key_file()
    try:
        stored = path.read_text(encoding="utf-8").strip()
        if stored:
            _cached_key = stored
            return _cached_key
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not read API key file %s: %s", path, exc)
    _cached_key = _write_new_key()
    logger.info(
        "Generated a new ClimateIQ API key — view it in Settings → API Access "
        "(needed by the dial and any direct API clients)"
    )
    return _cached_key


def rotate_api_key() -> str:
    """Replace the stored key with a fresh one.  Not allowed when env-fixed."""
    global _cached_key
    if is_key_from_env():
        raise RuntimeError("The API key is set by CLIMATEIQ_API_KEY and cannot be rotated here")
    _cached_key = _write_new_key()
    logger.info("ClimateIQ API key rotated")
    return _cached_key


def key_matches(candidate: str | None) -> bool:
    """Constant-time comparison against the active key."""
    if not candidate:
        return False
    return hmac.compare_digest(candidate.encode(), get_api_key().encode())


def _write_new_key() -> str:
    key = secrets.token_urlsafe(32)
    path = _key_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(key, encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    except OSError as exc:
        # Still usable for this process lifetime; logged so it's visible.
        logger.error("Could not persist API key to %s: %s", path, exc)
    return key
