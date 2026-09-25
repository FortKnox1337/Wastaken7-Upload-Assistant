"""Describe interactive questions to the WebUI without changing CLI validation."""

from __future__ import annotations

import contextlib
import contextvars
import functools
import json
import logging
import os
import sys
import threading
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any

from rich.style import Style
from rich.text import Text

from src.cogs.redaction import Redaction

PROMPT_STDOUT_PREFIX = "UA_PROMPT_JSON:"
_details: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("webui_prompt_details", default=None)
_question: contextvars.ContextVar[Callable[[], dict[str, Any]] | None] = contextvars.ContextVar("webui_prompt_question", default=None)
_output_lock = threading.Lock()
_check_messages: contextvars.ContextVar[_CheckMessages | None] = contextvars.ContextVar("webui_check_messages", default=None)


def _enabled() -> bool:
    return os.environ.get("UA_WEBUI_PROMPTS_STDOUT") == "1"


def _plain(value: object) -> str:
    text = Text.from_ansi(str(value)).plain
    with contextlib.suppress(Exception):
        text = Text.from_markup(text).plain
    return text.strip()


def build_check_review(tracker: str, messages: Iterable[str], *, kind: str = "upload") -> dict[str, Any]:
    """Expose the check's own explanations without terminal markup or secrets."""
    cleaned = []
    for message in messages:
        plain = _plain(message)
        # Keep JSON credentials protected even when a response spans lines.
        for start, end in reversed(Redaction.extract_json_blocks(plain)):
            with contextlib.suppress(json.JSONDecodeError):
                value = json.loads(plain[start:end])
                plain = plain[:start] + json.dumps(Redaction.redact_private_info(value)) + plain[end:]
        # Redact each line separately so a URL parameter cannot consume later
        # lines of a multi-line explanation.
        text = "\n".join(str(Redaction.redact_private_info(line)) for line in plain.splitlines())
        if text and text not in cleaned:
            cleaned.append(text)
    return {"tracker": tracker, "kind": kind, "messages": cleaned}


class _CheckMessages(logging.Handler):
    def __init__(self, tracker: str) -> None:
        super().__init__(level=logging.INFO)
        self.tracker = tracker
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        # Checks overlap across trackers and can call helpers in other threads.
        # Only retain messages from this check's context, never nearby log lines.
        if _check_messages.get() is self:
            self.messages.append(record.getMessage())


@contextlib.contextmanager
def capture_check_messages(logger: logging.Logger, tracker: str) -> Iterator[list[str]]:
    """Keep tracker explanations for the GUI while normal logging continues."""
    if not _enabled():
        yield []
        return
    handler = _CheckMessages(tracker)
    token = _check_messages.set(handler)
    logger.addHandler(handler)
    try:
        yield handler.messages
    finally:
        logger.removeHandler(handler)
        _check_messages.reset(token)
        handler.close()


def build_release_review(
    base_name: str,
    tracker_names: Mapping[str, str],
    lines: Iterable[str | tuple[str, str]],
    *,
    personal_release: bool = False,
    debug: bool = False,
) -> dict[str, Any]:
    """Serialize the confirmation's source values, before console formatting.

    Keep warnings already marked in the shared review visible outside the
    optional metadata details. Never extract names from rendered log lines.
    """
    fields: list[dict[str, str]] = []
    notices: list[dict[str, str]] = []
    if debug:
        notices.append({"text": "Debug run: nothing will be submitted to trackers. Images may still be uploaded.", "tone": "warning"})

    for item in lines:
        label, value = item if isinstance(item, tuple) else ("", item)
        text = _plain(value)
        if not text or (personal_release and text == "Personal Release!") or (debug and text.startswith("DEBUG:")):
            continue
        warning = False
        with contextlib.suppress(Exception):
            for span in Text.from_markup(str(value)).spans:
                style = Style.parse(span.style) if isinstance(span.style, str) else span.style
                if style.color and style.color.name in {"red", "yellow", "bright_red", "bright_yellow"}:
                    warning = True
        if label:
            fields.append({"label": _plain(label).rstrip(":"), "value": text})
        if warning or not label:
            notices.append({"text": f"{_plain(label).rstrip(':')}: {text}" if label else text, "tone": "warning" if warning else "info"})

    return {
        "base_name": base_name,
        "tracker_names": [{"tracker": tracker, "name": name} for tracker, name in sorted(tracker_names.items())],
        "flags": ["Personal release"] if personal_release else [],
        "fields": fields,
        "notices": notices,
    }


@contextlib.contextmanager
def prompt_details(**details: Any) -> Iterator[None]:
    """Attach choices or a form to an existing prompt; also propagates to threads."""
    token = _details.set(details)
    try:
        yield
    finally:
        _details.reset(token)


def _emit(event: dict[str, Any]) -> None:
    with _output_lock:
        sys.stdout.write(f"\n{PROMPT_STDOUT_PREFIX}{json.dumps(event, separators=(',', ':'))}\n")
        sys.stdout.flush()


def begin_input(prompt: str = "") -> str | None:
    if not _enabled():
        return None
    factory = _question.get()
    details = (
        factory()
        if factory
        else {
            "kind": "text" if prompt.strip() else "console",
            "question": _plain(prompt) or "This question needs the Console view.",
        }
    )
    details.update(_details.get() or {})
    check = _check_messages.get()
    if check is not None and details.get("kind") == "yes_no" and check.messages and not _details.get():
        # Some adapters ask about individual rules before returning their final
        # check result. Give those questions the same tracker-specific context.
        details["check_review"] = build_check_review(check.tracker, check.messages, kind="rules")
    prompt_id = uuid.uuid4().hex
    _emit({"op": "open", "prompt": {**details, "id": prompt_id}})
    return prompt_id


def end_input(prompt_id: str | None) -> None:
    if prompt_id:
        _emit({"op": "close", "id": prompt_id})


def install_cli_ui_prompts() -> None:
    """Wrap questions, leaving cli_ui responsible for retries, defaults and values."""
    if not _enabled():
        return
    import cli_ui

    for name, kind in (("ask_yes_no", "yes_no"), ("ask_string", "text"), ("ask_choice", "choice")):
        original = getattr(cli_ui, name)
        if getattr(original, "_ua_structured_prompt", False):
            continue

        def wrap(callback: Callable[..., Any], prompt_kind: str) -> Callable[..., Any]:
            @functools.wraps(callback)
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                def describe() -> dict[str, Any]:
                    result: dict[str, Any] = {
                        "kind": prompt_kind,
                        "question": " ".join(_plain(arg) for arg in args if not isinstance(arg, cli_ui.Color)),
                        "default": kwargs.get("default", False if prompt_kind == "yes_no" else None),
                    }
                    if prompt_kind == "choice":
                        # cli_ui sorts this list before input(). Read it now, so the
                        # numbered values always match the original validation loop.
                        describe_choice = kwargs.get("func_desc") or str
                        result["choices"] = [{"value": str(index), "label": _plain(describe_choice(choice))} for index, choice in enumerate(kwargs.get("choices", []), 1)]
                    return result

                token = _question.set(describe)
                try:
                    return callback(*args, **kwargs)
                finally:
                    _question.reset(token)

            wrapped._ua_structured_prompt = True  # type: ignore[attr-defined]
            return wrapped

        setattr(cli_ui, name, wrap(original, kind))
