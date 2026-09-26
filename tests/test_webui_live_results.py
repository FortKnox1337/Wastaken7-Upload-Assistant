# ruff: noqa: S101
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import trackerhandle, trackerstatus, webui_progress, webui_prompts
from src.meta import Meta
from src.webui_results import public_result_url, tracker_result
from web_ui import server


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://user:secret@example.test/1",
        "https://example.test/?api_key=secret",
        "https://example.test/?passkey=secret",
        "https://example.test/1 extra",
        "https://example.test/[REDACTED]",
    ],
)
def test_result_links_reject_private_or_invalid_urls(url):
    assert public_result_url(url) == ""


def test_debug_result_is_not_an_upload_and_has_no_torrent_link():
    status = {"upload_success": True, "uploaded_url": "https://tracker.example/torrents/42"}
    assert tracker_result("TEST", status)["url"].endswith("/42")
    assert tracker_result("TEST", status, debug=True) == {"tracker": "TEST", "outcome": "Debug completed", "detail": ""}
    assert "url" not in tracker_result("TEST", {**status, "upload_success": False})


def test_streamed_result_precedes_metadata_and_clears_old_detail():
    state = {}
    server._apply_progress_event(state, {"id": "tracker:TEST", "group": "tracker", "label": "TEST", "status": "Waiting", "detail": "Approved"})
    server._apply_progress_event(state, {"id": "tracker:TEST", "group": "tracker", "label": "TEST", "status": "Uploaded", "detail": "", "url": "https://tracker.example/42"})
    events = list(state["progress"].values())
    result = server._preview_tracker_results({"tracker_status": {"TEST": {"upload": True}}}, events)
    assert result == [{"tracker": "TEST", "outcome": "Uploaded", "detail": "", "url": "https://tracker.example/42"}]
    assert server._preview_tracker_results({"debug": True}, events) == [{"tracker": "TEST", "outcome": "Debug completed", "detail": ""}]
    server._apply_progress_event(state, {"id": "images:one", "group": "activity", "status": "completed"})
    server._apply_progress_event(state, {"op": "reset"})
    assert state["progress"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("client_fails", [False, True])
async def test_upload_lifecycle_reports_live_results_without_console_preferences(monkeypatch, debug, client_fails):
    events = []
    monkeypatch.setattr(webui_progress, "_emit", events.append)
    monkeypatch.setattr(trackerhandle, "TrackerSetup", lambda **_kwargs: SimpleNamespace(trackers_enabled=lambda meta: meta.trackers))
    monkeypatch.setattr(trackerhandle, "provision_tracker_torrents", AsyncMock())
    monkeypatch.setattr(trackerhandle, "check_tracker_image_hosts", AsyncMock())
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()

    class Tracker:
        torrent_url = "https://tracker.example/torrents/"

        def __init__(self, name):
            self.tracker = name

        async def upload(self, meta):
            if self.tracker == "SLOW":
                slow_started.set()
                await release_slow.wait()
            if self.tracker == "FAIL":
                raise RuntimeError("Request failed: https://tracker.example/?api_key=secret")
            if not debug:
                meta.tracker_status[self.tracker]["torrent_id"] = "42"
            return True

    names = ["FAST", "SLOW", "FAIL", "SKIP"]
    factories = {name: (lambda name=name, **_kwargs: Tracker(name)) for name in names}
    meta = Meta(name="Example", category="TV", trackers=names, debug=debug, print_tracker_links=False, print_tracker_messages=False)
    meta.tracker_status = {name: {"upload": name != "SKIP"} for name in names}
    meta.tracker_status["SKIP"]["skip_reason"] = "Duplicate found"
    client = SimpleNamespace(add_to_client=AsyncMock(side_effect=RuntimeError("Client unavailable") if client_fails else None))
    task = asyncio.create_task(trackerhandle.process_trackers(meta, {"DEFAULT": {"smart_image_host_selection": False}}, client, names, factories, [], []))
    await slow_started.wait()
    await asyncio.sleep(0)
    fast = [event for event in events if event.get("id") == "tracker:FAST"]
    assert any(event["status"] == ("Debug processing…" if debug else "Uploading…") for event in fast)
    assert fast[-1]["status"] == ("Debug completed" if debug else "Uploaded")
    assert fast[-1]["url"] == ("" if debug else "https://tracker.example/torrents/42")
    assert not task.done()
    release_slow.set()
    await task
    rows = {event["label"]: event for event in events if event.get("group") == "tracker"}
    assert rows["FAIL"]["status"] == "Failed"
    assert "secret" not in rows["FAIL"]["detail"]
    assert rows["SKIP"]["status"] == "Skipped"
    assert rows["SKIP"]["detail"] == "Duplicate found"
    assert meta.tracker_status["FAST"].get("uploaded_url", "") == ("" if debug else "https://tracker.example/torrents/42")


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [["FIRST"], ["FIRST", "SECOND"]])
@pytest.mark.parametrize("answer", [False, True])
async def test_single_and_multiple_upload_confirmations_keep_decisions(monkeypatch, names, answer):
    prompts = []
    monkeypatch.setenv("UA_WEBUI_PROMPTS_STDOUT", "1")
    monkeypatch.setattr(webui_prompts, "_emit", prompts.append)
    monkeypatch.setattr(webui_progress, "_emit", lambda _event: None)
    setup = SimpleNamespace(filter_unsupported_trackers=lambda _meta: None, check_banned_group=AsyncMock(return_value=False), get_torrent_claims=AsyncMock(return_value=False))
    monkeypatch.setattr(trackerstatus, "TrackerSetup", lambda **_kwargs: setup)
    monkeypatch.setattr(trackerstatus, "AvistaZNetworkRouter", lambda *_args: SimpleNamespace(apply=AsyncMock()))

    class Helper:
        dupe_check = AsyncMock(side_effect=lambda _dupes, meta, _name: (False, meta))

        async def prompt_yes_no(self, question, default=False):
            assert question == ("Upload?" if len(names) == 1 else "Upload to all?")
            assert default is False
            prompt_id = webui_prompts.begin_input()
            webui_prompts.end_input(prompt_id)
            return answer

    monkeypatch.setattr(trackerstatus, "UploadHelper", lambda _config: Helper())
    monkeypatch.setattr(trackerstatus, "DupeChecker", lambda _config: SimpleNamespace(filter_dupes=AsyncMock(return_value=[])))
    factories = {
        name: (lambda name=name, **_kwargs: SimpleNamespace(tracker=name, banned_groups=[], search_existing=AsyncMock(return_value=[]), get_name=AsyncMock(return_value=None)))
        for name in names
    }
    monkeypatch.setattr(trackerstatus, "tracker_class_map", factories)
    meta = Meta(name="Example", category="TV", trackers=names, imdb_id=123, debug=False)
    count = await trackerstatus.TrackerStatusManager({}).process_all_trackers(meta)
    review = prompts[0]["prompt"]
    assert [row["tracker"] for row in review["upload_review"]["trackers"]] == names
    assert all(row["detail"] == "No potential duplicates found" for row in review["upload_review"]["trackers"])
    assert review["question"] == ("Proceed with upload?" if len(names) == 1 else "Proceed with uploads to these trackers?")
    assert count == (len(names) if answer else 0)
    assert all(meta.tracker_status[name]["upload"] is answer for name in names)


@pytest.mark.asyncio
async def test_image_progress_reports_each_finished_image(monkeypatch, tmp_path):
    from src import uploadscreens

    events = []
    monkeypatch.setattr(webui_progress, "_emit", events.append)
    monkeypatch.setattr(uploadscreens, "screenshots_dir", lambda *_args: tmp_path)
    for name in ["one.png", "two.png"]:
        (tmp_path / name).write_bytes(b"image")

    async def upload(args):
        filename = args[0]
        await asyncio.sleep(0)
        return {
            "status": "success",
            "img_url": f"https://images.example/{filename}",
            "raw_url": f"https://images.example/{filename}",
            "web_url": f"https://images.example/{filename}",
        }

    monkeypatch.setattr(uploadscreens, "upload_image_task", upload)
    meta = Meta(base_dir=str(tmp_path), uuid="images", imghost="imgbox")
    config = {"DEFAULT": {"img_host_1": "imgbox", "image_upload_delay": 0}, "TRACKERS": {}}
    await uploadscreens._upload_screens(config, meta, 2, 1, 0, 2, [], {})
    assert [event["current"] for event in events] == [0, 1, 2]
    assert all(event["total"] == 2 for event in events)
    assert events[-1]["status"] == "completed"
    assert events[-1]["detail"] == "2 of 2 images uploaded successfully"


@pytest.mark.parametrize("message", [{"error": "Rejected", "api_key": "private-key"}, '{\n"error": "Rejected",\n"api_key": "private-key"\n}\nNext message'])
def test_failed_result_redacts_structured_and_multiline_responses(message):
    result = tracker_result("TEST", {"upload_success": False, "status_message": message})
    assert "private-key" not in result["detail"]
    assert "Rejected" in result["detail"]
    if isinstance(message, str):
        assert "Next message" in result["detail"]
