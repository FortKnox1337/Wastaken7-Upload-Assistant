# ruff: noqa: S101
import asyncio
import logging
from types import SimpleNamespace

import pytest

from src import trackerstatus
from src.meta import Meta


class _Helper:
    prompted = False
    answer = True

    async def prompt_yes_no(self, _message: str, default: bool = False) -> bool:
        del default
        self.prompted = True
        return self.answer


@pytest.mark.asyncio
async def test_failed_additional_check_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _Helper()
    monkeypatch.setattr(trackerstatus, "UploadHelper", lambda _config: helper)
    monkeypatch.setattr(trackerstatus.sys, "stdin", SimpleNamespace(closed=False, isatty=lambda: False))

    async def check(_meta: Meta) -> bool:
        return False

    result = await trackerstatus.TrackerStatusManager({})._run_additional_checks("TEST", SimpleNamespace(get_additional_checks=check), Meta(), helper)

    assert result is True
    assert helper.prompted is True


@pytest.mark.asyncio
async def test_failed_additional_check_is_not_overridden_when_stdin_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _Helper()
    monkeypatch.setattr(trackerstatus, "UploadHelper", lambda _config: helper)
    monkeypatch.setattr(trackerstatus.sys, "stdin", SimpleNamespace(closed=True))

    async def check(_meta: Meta) -> bool:
        return False

    result = await trackerstatus.TrackerStatusManager({})._run_additional_checks("TEST", SimpleNamespace(get_additional_checks=check), Meta(), helper)

    assert result is False
    assert helper.prompted is False


@pytest.mark.asyncio
async def test_failed_additional_check_is_not_overridden_when_prompt_reaches_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _Helper()
    monkeypatch.setattr(trackerstatus, "UploadHelper", lambda _config: helper)

    async def prompt_yes_no(_message: str, default: bool = False) -> bool:
        del default
        helper.prompted = True
        raise EOFError

    monkeypatch.setattr(helper, "prompt_yes_no", prompt_yes_no)

    async def check(_meta: Meta) -> bool:
        return False

    result = await trackerstatus.TrackerStatusManager({})._run_additional_checks("TEST", SimpleNamespace(get_additional_checks=check), Meta(), helper)

    assert result is False
    assert helper.prompted is True


@pytest.mark.asyncio
async def test_failed_additional_check_is_skipped_in_unattended_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _Helper()
    monkeypatch.setattr(trackerstatus, "UploadHelper", lambda _config: helper)

    async def check(_meta: Meta) -> bool:
        return False

    result = await trackerstatus.TrackerStatusManager({})._run_additional_checks("TEST", SimpleNamespace(get_additional_checks=check), Meta(unattended=True), helper)

    assert result is False
    assert helper.prompted is False


@pytest.mark.asyncio
async def test_gui_check_messages_stay_with_their_tracker_across_tasks_and_threads(monkeypatch):
    from src import webui_prompts as prompts

    monkeypatch.setenv("UA_WEBUI_PROMPTS_STDOUT", "1")
    monkeypatch.setattr(trackerstatus.sys, "stdin", SimpleNamespace(closed=False))
    events = []
    monkeypatch.setattr(prompts, "_emit", events.append)
    logger = trackerstatus.logger
    original_handlers = list(logger.handlers)
    monkeypatch.setattr(logger, "level", logging.DEBUG)
    first_started = asyncio.Event()
    second_started = asyncio.Event()

    class Helper:
        async def prompt_yes_no(self, _question, default=False):
            assert default is False
            prompt_id = prompts.begin_input()
            prompts.end_input(prompt_id)
            return False

    async def first(_meta):
        logger.info("[red]FIRST: Missing audio[/red]")
        logger.debug("FIRST: debug noise")
        first_started.set()
        await second_started.wait()
        await asyncio.to_thread(logger.warning, "FIRST: Threaded warning")
        return False

    async def second(_meta):
        await first_started.wait()
        logger.info("SECOND: Missing media ID")
        second_started.set()
        await asyncio.sleep(0)
        return False

    async def unrelated():
        await first_started.wait()
        logger.error("Unrelated background error")

    manager = trackerstatus.TrackerStatusManager({})
    await asyncio.gather(
        manager._run_additional_checks("FIRST", SimpleNamespace(get_additional_checks=first), Meta(), Helper()),
        manager._run_additional_checks("SECOND", SimpleNamespace(get_additional_checks=second), Meta(), Helper()),
        unrelated(),
    )
    reviews = {event["prompt"]["check_review"]["tracker"]: event["prompt"]["check_review"] for event in events if event["op"] == "open"}
    assert reviews["FIRST"]["messages"] == ["FIRST: Missing audio", "FIRST: Threaded warning"]
    assert reviews["SECOND"]["messages"] == ["SECOND: Missing media ID"]
    assert logger.handlers == original_handlers
    prompt_id = prompts.begin_input("Unrelated question")
    prompts.end_input(prompt_id)
    assert "check_review" not in events[-2]["prompt"]


@pytest.mark.asyncio
async def test_check_capture_cleans_up_after_cancellation_and_exceptions(monkeypatch):
    from src import webui_prompts as prompts

    monkeypatch.setenv("UA_WEBUI_PROMPTS_STDOUT", "1")
    original_handlers = list(trackerstatus.logger.handlers)

    async def cancelled(_meta):
        trackerstatus.logger.info("Cancelled check")
        raise asyncio.CancelledError

    def broken(_meta):
        trackerstatus.logger.info("Broken check")
        raise ValueError("Bad response")

    manager = trackerstatus.TrackerStatusManager({})
    for check, error in [(cancelled, asyncio.CancelledError), (broken, ValueError)]:
        with pytest.raises(error):
            await manager._run_additional_checks("TEST", SimpleNamespace(get_additional_checks=check), Meta(), _Helper())
        assert trackerstatus.logger.handlers == original_handlers
    events = []
    monkeypatch.setattr(prompts, "_emit", events.append)
    prompt_id = prompts.begin_input("Next question")
    prompts.end_input(prompt_id)
    assert "check_review" not in events[0]["prompt"]


@pytest.mark.asyncio
async def test_successful_checks_do_not_prompt_or_leak_messages(monkeypatch):
    monkeypatch.setenv("UA_WEBUI_PROMPTS_STDOUT", "1")
    original_handlers = list(trackerstatus.logger.handlers)

    def check(_meta):
        trackerstatus.logger.info("All checks passed")
        return True

    helper = _Helper()
    assert await trackerstatus.TrackerStatusManager({})._run_additional_checks("TEST", SimpleNamespace(get_additional_checks=check), Meta(), helper)
    assert not helper.prompted
    assert trackerstatus.logger.handlers == original_handlers
