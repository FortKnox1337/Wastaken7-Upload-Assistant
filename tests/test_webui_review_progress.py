# ruff: noqa: S101
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import trackerstatus, webui_progress
from src.meta import Meta
from src.trackersetup import TrackerSetup
from src.uphelper import UploadHelper
from web_ui import server


class ReviewTracker:
    reject_episode_if_season_pack_exists = False

    async def get_name(self, meta):
        return {"name": meta.name}


@pytest.mark.asyncio
async def test_pending_reviews_are_counted_before_waiting_for_an_answer(monkeypatch):
    state = {}
    events = []

    def receive(event):
        events.append(event)
        server._apply_progress_event(state, event)

    monkeypatch.setattr(webui_progress, "_emit", receive)
    started = asyncio.Event()
    release = asyncio.Event()
    helper = UploadHelper({"DEFAULT": {}})
    helper.tracker_class_map = {name: lambda **_kwargs: ReviewTracker() for name in ("FIRST", "SECOND", "CLEAR")}

    async def answer(question, *, default=False):
        assert default is False
        if "FIRST" in question:
            started.set()
            await release.wait()
        return True

    monkeypatch.setattr(helper, "prompt_yes_no", answer)
    first = asyncio.create_task(helper.dupe_check(["Existing release"], Meta(category="TV", name="Example"), "FIRST"))
    await asyncio.wait_for(started.wait(), 2)
    second = asyncio.create_task(helper.dupe_check(["Another release"], Meta(category="TV", name="Example"), "SECOND"))
    clear = asyncio.create_task(helper.dupe_check([], Meta(category="TV", name="Example"), "CLEAR"))
    await asyncio.sleep(0)
    try:
        queue = state["progress"]
        assert queue["duplicate-review:FIRST"]["status"] == "reviewing"
        assert queue["duplicate-review:FIRST"]["current"] == 1
        assert queue["duplicate-review:SECOND"]["status"] == "queued"
        assert queue["duplicate-review:CLEAR"]["status"] == "clear"
    finally:
        release.set()
        results = await asyncio.gather(first, second, clear)
    assert all(not skipped for skipped, _meta in results)
    assert [event["current"] for event in events if event["status"] == "reviewing"] == [1, 2]
    assert all(item["status"] == "done" for item in state["progress"].values())


@pytest.mark.asyncio
@pytest.mark.parametrize("fields,rejects", [({"unattended": True}, False), ({"ask_dupe": True}, False), ({"dupe": True}, False), ({"season_pack_exists": True}, True)])
async def test_automatic_duplicate_decisions_do_not_enter_review_queue(monkeypatch, fields, rejects):
    events = []
    monkeypatch.setattr(webui_progress, "_emit", events.append)
    tracker = ReviewTracker()
    tracker.reject_episode_if_season_pack_exists = rejects
    helper = UploadHelper({"DEFAULT": {}})
    helper.tracker_class_map = {"TEST": lambda **_kwargs: tracker}
    ask = AsyncMock(return_value=True)
    monkeypatch.setattr(helper, "prompt_yes_no", ask)
    await helper.dupe_check(["Existing release"], Meta(category="TV", name="Example", **fields), "TEST")
    ask.assert_not_awaited()
    assert not any(event["status"] in {"queued", "reviewing"} for event in events)


@pytest.mark.asyncio
async def test_follow_up_duplicate_questions_keep_one_tracker_number(monkeypatch):
    events = []
    monkeypatch.setattr(webui_progress, "_emit", events.append)
    helper = UploadHelper({"DEFAULT": {}})
    helper.tracker_class_map = {"FIRST": lambda **_kwargs: ReviewTracker(), "SECOND": lambda **_kwargs: ReviewTracker()}
    monkeypatch.setattr(helper, "prompt_yes_no", AsyncMock(side_effect=[False, True, True]))
    episode = {"id": 1, "name": "Example S01E01", "size": "256 MiB"}
    meta = Meta(category="TV", name="Example", tv_pack=True, season_pack_contains_episode=True)
    meta["FIRST_matched_episode_ids"] = [episode]
    skipped, _ = await helper.dupe_check([episode, {"id": 2, "name": "Example S01", "size": "2 GiB"}], meta, "FIRST")
    assert not skipped
    await helper.dupe_check(["Existing release"], Meta(category="TV", name="Example"), "SECOND")
    assert [event["current"] for event in events if event["status"] == "reviewing"] == [1, 1, 2]


@pytest.mark.asyncio
async def test_claim_reason_reaches_live_and_final_results_and_filtered_matches_need_no_review(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(webui_progress, "_emit", events.append)
    setup = TrackerSetup({})
    monkeypatch.setattr(setup, "filter_unsupported_trackers", lambda _meta: None)
    monkeypatch.setattr(setup, "check_banned_group", AsyncMock(return_value=False))
    monkeypatch.setattr(
        setup,
        "_create_tracker_instance",
        lambda _name: SimpleNamespace(get_type_id=AsyncMock(return_value={"WEBDL": 4}), get_resolution_id=AsyncMock(return_value={"2160p": 1})),
    )
    claims_dir = tmp_path / "data" / "banned"
    claims_dir.mkdir(parents=True)
    (claims_dir / "AITHER_claimed_releases.json").write_text(
        json.dumps({"extracted_data": [{"title": "Dark Matter", "season": 2, "tmdb_id": "196322", "resolutions": [1], "types": [4]}]}), encoding="utf-8"
    )

    async def claims(meta, name):
        return await setup.check_tracker_claims(meta, name) if name == "AITHER" else False

    monkeypatch.setattr(setup, "get_torrent_claims", claims)
    monkeypatch.setattr(trackerstatus, "TrackerSetup", lambda **_kwargs: setup)
    monkeypatch.setattr(trackerstatus, "AvistaZNetworkRouter", lambda *_args: SimpleNamespace(apply=AsyncMock()))
    names = ["AITHER", "FILTERED"]
    factories = {
        name: lambda name=name, **_kwargs: SimpleNamespace(
            tracker=name, banned_groups=[], search_existing=AsyncMock(return_value=["Unrelated release"]), get_name=AsyncMock(return_value=None)
        )
        for name in names
    }
    monkeypatch.setattr(trackerstatus, "tracker_class_map", factories)
    monkeypatch.setattr(trackerstatus, "DupeChecker", lambda _config: SimpleNamespace(filter_dupes=AsyncMock(return_value=[])))
    helper = UploadHelper({"DEFAULT": {}})
    helper.tracker_class_map = factories
    monkeypatch.setattr(helper, "prompt_yes_no", AsyncMock(return_value=True))
    monkeypatch.setattr(trackerstatus, "UploadHelper", lambda _config: helper)
    meta = Meta(
        base_dir=str(tmp_path), category="TV", name="Dark Matter S02", tmdb="196322", imdb_id=123, type="WEBDL", resolution="2160p", season_int=2, trackers=names, debug=True
    )
    assert await trackerstatus.TrackerStatusManager({}).process_all_trackers(meta) == 1
    reason = "Claimed match found at AITHER: Dark Matter, Season: 2, TMDB ID: 196322"
    assert meta.tracker_status["AITHER"]["skip_reason"] == reason
    assert meta.tracker_status["AITHER"]["upload"] is False
    assert meta.tracker_status["FILTERED"]["check_message"] == "No potential duplicates found"
    helper.prompt_yes_no.assert_not_awaited()
    claim_rows = [event for event in events if event.get("group") == "tracker" and event["label"] == "AITHER" and event["status"] == "Skipped"]
    assert claim_rows and all(event["detail"] == reason for event in claim_rows)
    assert server._preview_tracker_results(meta.to_dict())[0]["detail"] == reason
