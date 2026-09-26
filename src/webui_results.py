"""Structured tracker activity and results shared by the uploader and WebUI."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping
from urllib.parse import urlsplit

from rich.text import Text

from src.cogs.redaction import Redaction
from src.webui_progress import publish_progress


def public_result_url(value: object) -> str:
    """Only expose complete public HTTP links, never credential-bearing URLs."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return ""
    except ValueError:
        return ""
    if any(char.isspace() or ord(char) < 32 for char in value) or "[REDACTED]" in value:
        return ""
    if Redaction.redact_private_info(value) != value:
        return ""
    return value


def plain_detail(value: object) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(Redaction.redact_private_info(value))
    text = Text.from_ansi(str(value or "")).plain
    with contextlib.suppress(Exception):
        text = Text.from_markup(text).plain
    for start, end in reversed(Redaction.extract_json_blocks(text)):
        with contextlib.suppress(json.JSONDecodeError):
            value = json.loads(text[start:end])
            text = text[:start] + json.dumps(Redaction.redact_private_info(value)) + text[end:]
    return "\n".join(str(Redaction.redact_private_info(line)) for line in text.splitlines()).strip()


def tracker_result(tracker: str, status: Mapping[str, object], *, debug: bool = False) -> dict[str, str]:
    if status.get("upload_success") is True:
        outcome = "Debug completed" if debug else "Uploaded"
    elif status.get("upload_success") is False:
        outcome = "Failed"
    elif status.get("skipped") or status.get("upload") is False:
        outcome = "Skipped"
    else:
        outcome = "No upload result reported"
    detail = ""
    if outcome in {"Skipped", "Failed"}:
        detail = plain_detail(status.get("status_message"))
        if outcome == "Skipped":
            detail = str(status.get("skip_reason") or detail)
            if not detail:
                if status.get("redirected_to"):
                    detail = f"Redirected to {status['redirected_to']}"
                elif status.get("banned"):
                    detail = "Release group is banned"
                elif status.get("dupe"):
                    detail = "Duplicate found"
                else:
                    detail = "See Console for details"
    result = {"tracker": tracker, "outcome": outcome, "detail": plain_detail(detail)}
    if outcome == "Uploaded":
        url = public_result_url(status.get("uploaded_url")) or public_result_url(status.get("status_message"))
        if url:
            result["url"] = url
    return result


def publish_tracker(tracker: str, outcome: str, detail: str = "", url: str = "") -> None:
    publish_progress(f"tracker:{tracker}", tracker, status=outcome, detail=plain_detail(detail), group="tracker", url=public_result_url(url))


def publish_tracker_result(tracker: str, status: Mapping[str, object], *, debug: bool = False) -> None:
    result = tracker_result(tracker, status, debug=debug)
    publish_tracker(tracker, result["outcome"], result["detail"], result.get("url", ""))
