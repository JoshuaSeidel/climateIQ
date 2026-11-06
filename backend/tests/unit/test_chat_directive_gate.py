"""Unit tests for the chat directive-extraction gate."""

from __future__ import annotations

from backend.api.routes.chat import _should_extract_directives


def test_questions_skip_extraction() -> None:
    assert not _should_extract_directives("what is the temperature in the kitchen?")
    assert not _should_extract_directives("why is it so hot in the house?  ")


def test_statements_extract() -> None:
    assert _should_extract_directives("Oliver goes to bed at 7pm")


def test_empty_skips() -> None:
    assert not _should_extract_directives("   ")
