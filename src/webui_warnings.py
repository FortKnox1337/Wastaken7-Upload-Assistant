"""Publish non-blocking uploader warnings without changing Console logging."""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
from collections.abc import Iterator

from src.webui_progress import has_progress_callback, publish_progress
from src.webui_results import plain_detail

_tracker: contextvars.ContextVar[str] = contextvars.ContextVar("webui_warning_tracker", default="")


@contextlib.contextmanager
def warning_context(tracker: str) -> Iterator[None]:
    token = _tracker.set(tracker)
    try:
        yield
    finally:
        _tracker.reset(token)


class WebUIWarningHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)

    def emit(self, record: logging.LogRecord) -> None:
        if not has_progress_callback():
            return
        title = getattr(record, "webui_warning", "")
        if record.levelno != logging.WARNING and not title:
            return
        try:
            message = plain_detail(getattr(record, "webui_detail", None) or record.getMessage())
            if not message:
                return
            tracker = _tracker.get()
            label = plain_detail(title) if title else "Warning"
            identity = hashlib.sha256(f"{tracker}\n{label}\n{message}".encode()).hexdigest()[:20]
            publish_progress(f"warning:{identity}", label, detail=message, status="warning", group="warning", tracker=tracker)
        except Exception:
            self.handleError(record)
