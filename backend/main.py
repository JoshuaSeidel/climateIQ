"""Backwards-compatible entry point — delegates to backend.api.main."""

from backend.api.main import app

__all__ = ["app"]
