# ruff: noqa: S101
import asyncio
import logging

import pytest

from src import tvdb, webui_progress
from src.console import logger
from src.webui_warnings import WebUIWarningHandler, warning_context
from web_ui import server


@pytest.fixture
def warning_events(monkeypatch):
    events = []
    monkeypatch.setattr(webui_progress, "_callback", events.append)
    return events


def test_warnings_are_redacted_deduplicated_and_retained_after_phase_reset(warning_events):
    for _ in range(2):
        logger.warning("[yellow]Request unavailable: https://example.test/?api_key=secret[/yellow]")
    assert len(warning_events) == 2
    assert warning_events[0]["id"] == warning_events[1]["id"]
    assert "secret" not in warning_events[0]["detail"]
    assert "[yellow]" not in warning_events[0]["detail"]
    state = server._make_process_state("example.mkv", "")
    for event in warning_events:
        server._apply_progress_event(state, event)
    server._apply_progress_event(state, {"id": "check", "group": "external"})
    server._apply_progress_event(state, {"op": "reset"})
    items = server._progress_items_for_process(state)
    assert len(items) == 1
    assert items[0]["group"] == "warning"
    assert items[0]["tracker"] == ""
    assert server._make_process_state("next.mkv", "")["progress"] == {}


def test_ordinary_progress_and_errors_are_not_warning_notices(warning_events):
    logger.info("[yellow]Processing approved uploads in the background...[/yellow]")
    logger.debug("Diagnostic details")
    logger.error("An error handled by the tracker result")
    assert warning_events == []


def test_console_only_run_keeps_original_logging(monkeypatch, caplog):
    monkeypatch.delenv("UA_WEBUI_PROGRESS_STDOUT", raising=False)
    monkeypatch.setattr(webui_progress, "_callback", None)
    events = []
    monkeypatch.setattr(webui_progress, "_emit", events.append)
    with caplog.at_level(logging.WARNING, logger=logger.name):
        logger.warning("A non-blocking warning")
    assert "A non-blocking warning" in caplog.text
    assert events == []
    assert sum(isinstance(handler, WebUIWarningHandler) for handler in logger.handlers) == 1


def test_missing_tvdb_key_has_readable_notice_and_preserves_console_message(monkeypatch, warning_events, caplog):
    monkeypatch.setattr(tvdb, "tvdb", None)
    monkeypatch.setattr(tvdb, "_tvdb_error_reported", False)
    with caplog.at_level(logging.INFO, logger=logger.name):
        assert tvdb._get_tvdb_or_warn({"DEFAULT": {}}) is None
        assert tvdb._get_tvdb_or_warn({"DEFAULT": {}}) is None
    assert "TVDB API key is missing in config.py" in caplog.text
    assert len(warning_events) == 1
    assert warning_events[0]["label"] == "TVDB lookup skipped"
    assert warning_events[0]["detail"] == "No TVDB API key is configured. The upload will continue without TVDB metadata."


@pytest.mark.asyncio
async def test_concurrent_tracker_warnings_keep_their_own_context(warning_events):
    async def check(name):
        with warning_context(name):
            await asyncio.sleep(0)
            logger.warning("Optional check unavailable")
            await asyncio.to_thread(logger.warning, "Threaded check unavailable")

    await asyncio.gather(check("FIRST"), check("SECOND"))
    logger.warning("General warning")
    assert [event["tracker"] for event in warning_events].count("FIRST") == 2
    assert [event["tracker"] for event in warning_events].count("SECOND") == 2
    assert warning_events[-1]["tracker"] == ""
    assert len({event["id"] for event in warning_events}) == 5


def test_tracker_context_is_restored_after_failure(warning_events):
    with pytest.raises(ValueError), warning_context("FAILED"):
        raise ValueError("Check failed")
    logger.warning("General warning")
    assert warning_events[0]["tracker"] == ""
