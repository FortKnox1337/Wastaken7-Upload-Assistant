# ruff: noqa: S101, S603
import io
import json
import os
import subprocess
import sys
import time

import pytest

import web_ui.server as server
from src.webui_prompts import PROMPT_STDOUT_PREFIX, build_release_review


def _run_questions(script, answers, *, enabled=True):
    env = os.environ.copy()
    env["UA_WEBUI_PROMPTS_STDOUT"] = "1" if enabled else "0"
    env["PYTHONIOENCODING"] = "utf-8"
    # conftest isolates APPDATA, which otherwise hides Windows user-site packages
    # from fresh interpreters. Preserve the already-loaded dependency locations.
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    result = subprocess.run(
        [
            sys.executable,
            "-u",
            "-c",
            "import src.console; import cli_ui; from src.webui_prompts import install_cli_ui_prompts, prompt_details; install_cli_ui_prompts(); " + script,
        ],
        input=answers,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    events = [json.loads(line.removeprefix(PROMPT_STDOUT_PREFIX)) for line in result.stdout.splitlines() if line.startswith(PROMPT_STDOUT_PREFIX)]
    return result.stdout, events


def test_cli_questions_unchanged_without_webui():
    output, events = _run_questions("print('ANSWER:', cli_ui.ask_yes_no('Continue?', default=True))", "\n", enabled=False)
    assert "ANSWER: True" in output
    assert events == []


def test_invalid_answer_retries_with_new_id_and_keeps_default():
    output, events = _run_questions("print('ANSWER:', cli_ui.ask_yes_no(cli_ui.green, 'Continue?', default=True))", "invalid\n\n")
    opened = [event["prompt"] for event in events if event["op"] == "open"]
    assert len(opened) == 2
    assert opened[0]["id"] != opened[1]["id"]
    assert opened[0]["question"] == "Continue?"
    assert opened[0]["default"] is True
    assert "ANSWER: True" in output
    assert [event["op"] for event in events] == ["open", "close", "open", "close"]


def test_sorted_choice_numbers_follow_cli_ui_and_return_original_value():
    output, events = _run_questions(
        "choices = [{'name':'Zulu'}, {'name':'Alpha'}]; print('ANSWER:', cli_ui.ask_choice('Choose', choices=choices, func_desc=lambda item: item['name']))", "1\n"
    )
    assert events[0]["prompt"]["choices"] == [{"value": "1", "label": "Alpha"}, {"value": "2", "label": "Zulu"}]
    assert "ANSWER: {'name': 'Alpha'}" in output


def test_details_propagate_through_prompt_thread_and_do_not_leak():
    script = """
import asyncio
from src.console import prompt_in_thread
async def run():
    with prompt_details(kind='choice', question='Which match?', choices=[{'value':'1','label':'Example'}]):
        print('MATCH:', await prompt_in_thread(cli_ui.ask_string, 'Legacy question'))
    print('NEXT:', cli_ui.ask_string('Title?', default='Example'))
asyncio.run(run())
"""
    output, events = _run_questions(script, "1\n\n")
    assert events[0]["prompt"]["kind"] == "choice"
    assert events[0]["prompt"]["question"] == "Which match?"
    assert events[2]["prompt"]["kind"] == "text"
    assert "NEXT: Example" in output


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, expected, searches", [("1", (101, "TV"), 1), ("movie/777", (777, "MOVIE"), 1), ("", (303, "MOVIE"), 2)])
async def test_tmdb_choice_artwork_keeps_selection_manual_id_and_search_retry(monkeypatch, answer, expected, searches):
    import httpx

    import src.tmdb as tmdb
    import src.webui_prompts as prompts
    from src.args import Args

    requests = []
    events = []

    def respond(request):
        requests.append(request.url.path)
        results = (
            [
                {"id": 101, "name": "Haven", "original_name": "Haven", "first_air_date": "2010-07-09", "poster_path": "/haven.jpg"},
                {"id": 202, "name": "Haven", "original_name": "Haven", "first_air_date": "2001-02-11", "poster_path": None},
            ]
            if len(requests) == 1
            else [{"id": 303, "title": "Haven"}]
        )
        return httpx.Response(200, json={"results": results})

    def ask(_question):
        prompt_id = prompts.begin_input()
        prompts.end_input(prompt_id)
        return answer

    client = httpx.AsyncClient
    monkeypatch.setattr(tmdb.httpx, "AsyncClient", lambda: client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(tmdb, "parser", Args({}))
    monkeypatch.setattr(tmdb.cli_ui, "ask_string", ask)
    monkeypatch.setenv("UA_WEBUI_PROMPTS_STDOUT", "1")
    monkeypatch.setattr(prompts, "_emit", events.append)

    assert await tmdb.get_tmdb_id("Haven", None, "TV", "Haven.mkv") == expected
    assert len(requests) == searches
    prompt = events[0]["prompt"]
    assert prompt["choices"][0]["poster_url"] == "https://image.tmdb.org/t/p/w185/haven.jpg"
    assert prompt["choices"][1]["poster_url"] is None
    assert prompt["empty_label"] == "None of these — search again"
    assert prompt["choices"][0]["value"] == "1"
    if not answer:
        assert requests == ["/3/search/tv", "/3/search/movie"]


def test_release_review_keeps_metadata_warnings_and_literal_release_names():
    review = build_release_review(
        "Example [1080p]-GROUP",
        {"SECOND": "Example & Example [1080p]", "FIRST": "Example 1080p"},
        [
            "[bold red]DEBUG: True - Will not actually upload![/bold red]",
            ("Title", "Example"),
            ("Author", "[bold red]⚠️ Missing[/bold red]"),
            ("Music validation", r"[yellow]Review \[album] tags[/yellow]"),
            ("Edition", ""),
            "[bold green]Personal Release![/bold green]",
            "",
        ],
        personal_release=True,
        debug=True,
    )
    assert review["base_name"] == "Example [1080p]-GROUP"
    assert review["tracker_names"] == [{"tracker": "FIRST", "name": "Example 1080p"}, {"tracker": "SECOND", "name": "Example & Example [1080p]"}]
    assert review["flags"] == ["Personal release"]
    assert {field["label"]: field["value"] for field in review["fields"]} == {"Title": "Example", "Author": "⚠️ Missing", "Music validation": "Review [album] tags"}
    assert all(notice["tone"] == "warning" for notice in review["notices"])
    assert [notice["text"] for notice in review["notices"]] == [
        "Debug run: nothing will be submitted to trackers. Images may still be uploaded.",
        "Author: ⚠️ Missing",
        "Music validation: Review [album] tags",
    ]


@pytest.mark.asyncio
async def test_confirmation_passes_generated_tracker_names_to_gui_without_changing_console(monkeypatch):
    import src.webui_prompts as prompts
    from src.meta import Meta
    from src.uphelper import UploadHelper

    class Tracker:
        def __init__(self, result):
            self.result = result

        async def get_name(self, _meta):
            return self.result

    class FailedTracker:
        async def get_name(self, _meta):
            raise ValueError("Name generation failed")

    events, questions, logs = [], [], []

    def ask_yes_no(question, *, default=False):
        questions.append((question, default))
        prompt_id = prompts.begin_input()
        prompts.end_input(prompt_id)
        return False

    monkeypatch.setenv("UA_WEBUI_PROMPTS_STDOUT", "1")
    monkeypatch.setattr(prompts, "_emit", events.append)
    monkeypatch.setattr("src.uphelper.cli_ui.ask_yes_no", ask_yes_no)
    monkeypatch.setattr("src.uphelper.logger.info", lambda text, **_kwargs: logs.append(text))
    helper = UploadHelper({"DEFAULT": {"sfx_on_prompt": False}})
    helper.tracker_class_map = {
        "HOMIEHELPDESK": lambda **_kwargs: Tracker({"name": "Haven S01 1080p AMZN WEB-DL-FORTKNOX"}),
        "AITHER": lambda **_kwargs: Tracker("Haven S01 1080p WEB-DL H.264-FORTKNOX"),
        "FAILED": lambda **_kwargs: FailedTracker(),
    }
    meta = Meta(category="TV", title="Haven", year="2010", name="Haven S01 Base Name", personalrelease=True, trackers=["HOMIEHELPDESK", "AITHER", "FAILED", "MANUAL", "USENET"])
    assert await helper.get_confirmation(meta) is False
    prompt = events[0]["prompt"]
    assert questions == [("Is this correct?", False)]
    assert prompt["kind"] == "yes_no"
    assert prompt["question"] == "Are these details correct?"
    assert prompt["review"]["base_name"] == meta.name
    assert prompt["review"]["tracker_names"] == [
        {"tracker": "AITHER", "name": "Haven S01 1080p WEB-DL H.264-FORTKNOX"},
        {"tracker": "HOMIEHELPDESK", "name": "Haven S01 1080p AMZN WEB-DL-FORTKNOX"},
    ]
    assert prompt["review"]["flags"] == ["Personal release"]
    assert any("FAILED" in notice["text"] for notice in prompt["review"]["notices"])
    assert any("Base Name:" in line for line in logs)
    assert any("HOMIEHELPDESK:" in line for line in logs)
    prompts.begin_input("Next question")
    assert "review" not in events[-1]["prompt"]


def test_eof_closes_question_and_plain_input_falls_back_to_console():
    output, events = _run_questions("\ntry:\n input()\nexcept EOFError:\n print('EOF')", "")
    assert events[0]["prompt"]["kind"] == "console"
    assert events[-1]["op"] == "close"
    assert "EOF" in output


def test_background_output_cannot_clear_question_or_reopen_answered_question(monkeypatch):
    state = server._make_process_state("example", "")
    monkeypatch.setitem(server.active_processes, "structured", state)
    prompt = {"id": "first", "kind": "yes_no", "question": "Continue?"}
    assert server._set_process_prompt_if_current("structured", state, {"op": "open", "prompt": prompt})
    server._set_process_awaiting_input_if_current("structured", state, False)
    assert state["awaiting_input"] is True
    state["prompt"] = None
    state["awaiting_input"] = False
    server._set_process_awaiting_input_if_current("structured", state, True)
    assert state["awaiting_input"] is False
    assert not server._set_process_prompt_if_current("structured", state, {"op": "close", "id": "stale"})
    assert server._set_process_prompt_if_current("structured", state, {"op": "close", "id": "first"})


def test_input_rejects_stale_and_duplicate_answers_and_preserves_new_question(monkeypatch):
    class Process:
        stdin = io.StringIO()

        def poll(self):
            return None

    process = Process()
    state = {**server._make_process_state("example", ""), "process": process}
    monkeypatch.setitem(server.active_processes, "answers", state)
    monkeypatch.setattr(server, "_is_authenticated", lambda: True)
    monkeypatch.setattr(server, "_verify_csrf_header", lambda: True)
    monkeypatch.setattr(server, "_verify_same_origin", lambda: True)
    client = server.app.test_client()
    server._set_process_prompt_if_current("answers", state, {"op": "open", "prompt": {"id": "one", "kind": "yes_no"}})

    def send(prompt_id):
        return client.post("/api/input", json={"session_id": "answers", "input": "yes", "prompt_id": prompt_id})

    assert send("old").status_code == 409
    assert send("one").status_code == 200
    assert send("one").status_code == 409
    server._set_process_prompt_if_current("answers", state, {"op": "open", "prompt": {"id": "two", "kind": "text"}})
    assert send("one").status_code == 409
    assert state["prompt"]["id"] == "two"
    assert process.stdin.getvalue() == "yes\n"


def test_tracker_result_never_treats_eligibility_as_upload_success():
    results = server._preview_tracker_results(
        {"tracker_status": {"A": {"upload": True}, "B": {"upload_success": True}, "C": {"upload_success": False}, "D": {"skipped": True}}}
    )
    assert [result["outcome"] for result in results] == ["No upload result reported", "Uploaded", "Failed", "Skipped"]


@pytest.mark.asyncio
async def test_category_skip_reason_survives_final_metadata_export(tmp_path):
    from src.cogs.redaction import Redaction
    from src.meta import Meta
    from src.trackersetup import TrackerSetup

    meta = Meta(category="TV", trackers=["REELFLIX", "RETROMOVIESCLUB", "ANTHELION"], debug=True, base_dir=str(tmp_path), uuid="skipped")
    TrackerSetup({"TRACKERS": {}}).filter_unsupported_trackers(meta)
    assert meta.trackers == []
    (tmp_path / "tmp" / "skipped").mkdir(parents=True)
    await Redaction.clean_meta_for_export(meta)

    saved = json.loads((tmp_path / "tmp" / "skipped" / "meta.json").read_text(encoding="utf-8"))
    results = server._preview_tracker_results(saved)
    assert len(results) == 3
    assert all(result["outcome"] == "Skipped" and result["detail"] == "TV is not supported" for result in results)


def test_tracker_results_explain_known_statuses_without_inventing_missing_reasons():
    results = server._preview_tracker_results(
        {
            "tracker_status": {
                "REDIRECT": {"upload": False, "redirected_to": "CINEMAZ"},
                "BANNED": {"upload": False, "banned": True},
                "DUPE": {"upload": False, "dupe": True},
                "UNKNOWN": {"skipped": True},
                "SUCCESS": {"upload_success": True, "skip_reason": "Old reason"},
                "FAILED": {"upload_success": False, "status_message": "\x1b[31m[red]Request failed: https://tracker.example/api?api_key=secret-token[/red]\x1b[0m"},
            }
        }
    )
    details = {result["tracker"]: result["detail"] for result in results}
    assert details["REDIRECT"] == "Redirected to CINEMAZ"
    assert details["BANNED"] == "Release group is banned"
    assert details["DUPE"] == "Duplicate found"
    assert details["UNKNOWN"] == "See Console for details"
    assert details["SUCCESS"] == ""
    assert details["FAILED"] == "Request failed: https://tracker.example/api?api_key=[REDACTED]"


@pytest.mark.parametrize("preview_fails", [False, True])
def test_exit_reports_result_even_when_final_preview_is_unavailable(tmp_path, monkeypatch, preview_fails):
    class FinishedProcess:
        stdin = io.StringIO()
        stdout = io.StringIO()
        stderr = io.StringIO()

        def poll(self):
            return 0

        def wait(self):
            return 0

    def preview(_session_id):
        if preview_fails:
            raise OSError("Preview no longer available")
        return {"title": "Example"}

    monkeypatch.setattr(server, "_is_authenticated", lambda: True)
    monkeypatch.setattr(server, "_verify_csrf_header", lambda: True)
    monkeypatch.setattr(server, "_resolve_user_path", lambda *_args, **_kwargs: str(tmp_path))
    monkeypatch.setattr(server, "_assert_safe_resolved_path", lambda _: None)
    monkeypatch.setattr(server, "_validate_upload_assistant_args", lambda args: args)
    monkeypatch.setattr(server, "_spawn_webui_upload_process", lambda *_args: (FinishedProcess(), "subprocess"))
    monkeypatch.setattr(server, "_find_execution_preview", preview)
    response = server.app.test_client().post("/api/execute", json={"path": str(tmp_path), "session_id": "final-preview-test"})
    events = [json.loads(line.removeprefix("data: ")) for line in response.get_data(as_text=True).splitlines() if line.startswith("data: ")]
    assert events[-1] == {"type": "exit", "code": 0, "media": None if preview_fails else {"title": "Example"}}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ConPTY transport")
def test_long_choice_records_survive_conpty():
    script = (
        "from src.webui_prompts import begin_input,end_input,prompt_details; "
        "ctx=prompt_details(kind='choice',question='Choose',choices=[{'value':str(i),'label':'Item','detail':'a'*250} for i in range(8)]); "
        "ctx.__enter__(); pid=begin_input(); end_input(pid); print('FINISHED',flush=True)"
    )
    env = server._webui_subprocess_env()
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    process, mode = server._spawn_webui_upload_process([sys.executable, "-u", "-c", script], server.CODE_DIR, env)
    output = ""
    try:
        assert mode == "conpty"
        deadline = time.monotonic() + 10
        while "FINISHED" not in output and time.monotonic() < deadline:
            try:
                output += process.read(1024)
            except EOFError:
                break
        events = [server._subprocess_prompt_event(line) for line in output.splitlines() if line.startswith(PROMPT_STDOUT_PREFIX)]
        assert events[0] is not None
        assert len(events[0]["prompt"]["choices"]) == 8
        assert events[1]["id"] == events[0]["prompt"]["id"]
    finally:
        if process.poll() is None:
            server._terminate_process_tree(process)
        server._close_webui_process_io(process)


@pytest.mark.parametrize("csrf,origin,status", [(False, True, 403), (True, False, 403), (True, True, 404)])
def test_structured_input_keeps_auth_checks_and_requires_active_session(monkeypatch, csrf, origin, status):
    monkeypatch.setattr(server, "_is_authenticated", lambda: True)
    monkeypatch.setattr(server, "_verify_csrf_header", lambda: csrf)
    monkeypatch.setattr(server, "_verify_same_origin", lambda: origin)
    response = server.app.test_client().post("/api/input", json={"session_id": "missing-prompt-session", "input": "yes", "prompt_id": "one"})
    assert response.status_code == status


DUPE_REVIEW_SETUP = """
import asyncio
from src.meta import Meta
from src.uphelper import UploadHelper
class Tracker:
    async def get_name(self, meta):
        return {'name': meta.name + ' [tracker name]'}
helper = UploadHelper({'DEFAULT': {}})
helper.tracker_class_map = {name: lambda **kwargs: Tracker() for name in ('ZENITH', 'AITHER')}
meta = Meta(category='TV', name='Example S01', source_size=1024 ** 3)
"""


def test_duplicate_review_uses_console_differences_and_safe_source_fields():
    script = (
        DUPE_REVIEW_SETUP
        + """
dupes = [
    {'name': 'Example [GROUP]', 'link': 'https://tracker.example/1', 'size': '1.25 GiB', 'download': 'private-token', 'files': ['private-path']},
    {'name': 'Same link again', 'link': 'https://tracker.example/1', 'size': '1.25 GiB'},
    {'name': 'Smaller', 'size': '768 MiB'},
    {'name': 'Same size', 'size': 1024 ** 3},
    {'name': '<img src=x onerror=alert(1)>', 'link': 'javascript:alert(1)', 'size': 'unknown'},
    'Legacy name [GROUP]',
]
print('SKIPPED:', asyncio.run(helper.dupe_check(dupes, meta, 'ZENITH'))[0])
print('NEXT:', cli_ui.ask_yes_no('Continue?', default=True))
"""
    )
    output, events = _run_questions(script, "no\nyes\n")
    prompt = events[0]["prompt"]
    review = prompt["duplicate_review"]
    assert prompt["question"] == "Upload to ZENITH anyway?"
    assert prompt["kind"] == "yes_no"
    assert prompt["default"] is False
    assert review["kind"] == "potential"
    assert review["tracker"] == "ZENITH"
    assert review["upload_name"] == "Example S01 [tracker name]"
    assert review["upload_size"] == 1024**3
    assert len(review["entries"]) == 5
    larger, smaller, same, unknown, legacy = review["entries"]
    assert larger == {"name": "Example [GROUP]", "url": "https://tracker.example/1", "size": 1280 * 1024**2, "difference": {"mb": 256, "percent": 25}}
    assert smaller["difference"] == {"mb": -256, "percent": -25}
    assert same["difference"] == {"mb": 0, "percent": 0}
    assert unknown == {"name": "<img src=x onerror=alert(1)>", "size": None}
    assert legacy == {"name": "Legacy name [GROUP]"}
    assert "+256 MB / +25%" in output and "-256 MB / -25%" in output
    assert "SKIPPED: True" in output
    assert "duplicate_review" not in events[2]["prompt"]


@pytest.mark.parametrize("show_diff, source_size", [(False, 1024**3), (True, None)])
def test_duplicate_review_respects_hidden_differences_and_missing_upload_size(show_diff, source_size):
    script = (
        DUPE_REVIEW_SETUP
        + f"""
helper.default_config['show_dupe_size_diff'] = {show_diff!r}
meta.source_size = {source_size!r}
print('SKIPPED:', asyncio.run(helper.dupe_check([{{'name': 'Existing', 'size': '2 GiB'}}], meta, 'ZENITH'))[0])
"""
    )
    output, events = _run_questions(script, "yes\n")
    review = events[0]["prompt"]["duplicate_review"]
    assert review["show_size_difference"] is show_diff
    assert review["entries"][0]["size"] == 2 * 1024**3
    assert "difference" not in review["entries"][0]
    assert "SKIPPED: False" in output


@pytest.mark.parametrize("tracker, answer, skipped, trumping", [("ZENITH", "yes", False, False), ("ZENITH", "no", True, False), ("AITHER", "yes", False, True)])
def test_exact_duplicate_review_uses_matched_entry_and_preserves_decisions(tracker, answer, skipped, trumping):
    script = (
        DUPE_REVIEW_SETUP
        + f"""
meta.filename_match = 'Exact [GROUP] = https://tracker.example/2'
meta.file_count_match = 13
meta['{tracker}_matched_id'] = 2
meta['{tracker}_matched_name'] = 'Exact [GROUP]'
meta['{tracker}_matched_link'] = 'https://tracker.example/2'
dupes = [{{'id': 1, 'name': 'Other', 'size': '2 GiB'}}, {{'id': 2, 'name': 'Exact [GROUP]', 'link': 'https://tracker.example/2', 'size': '1 GiB'}}]
print('SKIPPED:', asyncio.run(helper.dupe_check(dupes, meta, '{tracker}'))[0])
print('TRUMPING:', meta.were_trumping)
"""
    )
    output, events = _run_questions(script, answer + "\n")
    review = events[0]["prompt"]["duplicate_review"]
    assert review["kind"] == "exact"
    assert [entry["name"] for entry in review["entries"]] == ["Exact [GROUP]"]
    assert review["entries"][0]["difference"] == {"mb": 0, "percent": 0}
    assert bool(review["notices"]) is (tracker == "AITHER")
    assert f"SKIPPED: {skipped}" in output
    assert f"TRUMPING: {trumping}" in output


def test_season_pack_review_only_shows_matched_pack_and_keeps_warning():
    script = (
        DUPE_REVIEW_SETUP
        + """
meta.season_pack_exists = True
meta.season_pack_name = 'Example S01 pack'
meta.season_pack_link = 'https://tracker.example/pack'
meta.season_pack_id = 42
entries = [{'name': 'Episode', 'size': 123}, {'id': 42, 'name': meta.season_pack_name, 'link': meta.season_pack_link, 'size': '2 GiB'}]
print('SKIPPED:', asyncio.run(helper.dupe_check(entries, meta, 'ZENITH'))[0])
"""
    )
    output, events = _run_questions(script, "no\n")
    review = events[0]["prompt"]["duplicate_review"]
    assert review["kind"] == "season_pack"
    assert [entry["name"] for entry in review["entries"]] == ["Example S01 pack"]
    assert review["entries"][0]["difference"] == {"mb": 1024, "percent": 100}
    assert "Ensure your upload is not part of this season pack" in review["notices"][0]
    assert "SKIPPED: True" in output


def test_declining_episode_trump_updates_next_duplicate_review():
    script = (
        DUPE_REVIEW_SETUP
        + """
meta.tv_pack = True
meta.tag = '-GROUP'
meta.season_pack_contains_episode = True
episode = {'id': 1, 'name': 'Example S01E01-OTHER', 'link': 'https://tracker.example/1', 'size': '256 MiB'}
meta['ZENITH_matched_episode_ids'] = [episode]
entries = [episode, {'id': 2, 'name': 'Example S01 full pack', 'size': '2 GiB'}]
print('SKIPPED:', asyncio.run(helper.dupe_check(entries, meta, 'ZENITH'))[0])
print('TRUMPING:', meta.were_trumping)
"""
    )
    output, events = _run_questions(script, "no\nyes\n")
    reviews = [event["prompt"]["duplicate_review"] for event in events if event["op"] == "open"]
    assert reviews[0]["kind"] == "trumpable"
    assert reviews[0]["entries"][0]["name"] == "Example S01E01-OTHER"
    assert any("different group" in notice for notice in reviews[0]["notices"])
    assert reviews[1]["kind"] == "potential"
    assert [entry["name"] for entry in reviews[1]["entries"]] == ["Example S01 full pack"]
    assert "SKIPPED: False" in output
    assert "TRUMPING: False" in output


@pytest.mark.parametrize("answer, expected", [("yes", True), ("no", False)])
def test_trumpable_review_preserves_trumping_choice(answer, expected):
    script = (
        DUPE_REVIEW_SETUP
        + """
meta.trumpable_id = 1
meta['ZENITH_matched_id'] = 1
entries = [{'id': 1, 'name': 'Trumpable release', 'trumpable': True, 'size': '768 MiB'}]
print('SKIPPED:', asyncio.run(helper.dupe_check(entries, meta, 'ZENITH'))[0])
print('TRUMPING:', meta.were_trumping)
print('REASON:', meta.trump_reason)
"""
    )
    output, events = _run_questions(script, answer + "\nno\n")
    assert events[0]["prompt"]["duplicate_review"]["kind"] == "trumpable"
    assert f"TRUMPING: {expected}" in output
    assert f"SKIPPED: {not expected}" in output
    if expected:
        assert "REASON: trumpable_release" in output
    else:
        assert events[2]["prompt"]["duplicate_review"]["kind"] == "potential"


def test_duplicate_console_mode_keeps_prompt_and_size_output_without_records():
    script = (
        DUPE_REVIEW_SETUP
        + """
print('SKIPPED:', asyncio.run(helper.dupe_check([{'name': 'Example [GROUP]', 'size': '768 MiB'}], meta, 'ZENITH'))[0])
"""
    )
    output, events = _run_questions(script, "no\n", enabled=False)
    assert "Upload to ZENITH anyway?" in output
    assert "-256 MB / -25%" in output
    assert "SKIPPED: True" in output
    assert events == []


def test_bdinfo_comparison_keeps_its_prompt_and_output_before_duplicate_review():
    script = (
        DUPE_REVIEW_SETUP
        + """
import src.uphelper as uphelper
uphelper.has_bdinfo_content = lambda entry: True
uphelper.compare_bdinfo = lambda meta, entry, tracker: ('Comparison warning', 'Comparison results')
meta.is_disc = 'BDMV'
print('SKIPPED:', asyncio.run(helper.dupe_check([{'name': 'Existing disc', 'size': '2 GiB'}], meta, 'ZENITH'))[0])
"""
    )
    output, events = _run_questions(script, "yes\nno\n")
    opened = [event["prompt"] for event in events if event["op"] == "open"]
    assert opened[0]["question"] == "Found BDInfo content in potential duplicates. Perform a comparison?"
    assert opened[0]["default"] is True
    assert "duplicate_review" not in opened[0]
    assert opened[1]["duplicate_review"]["entries"][0]["name"] == "Existing disc"
    assert "Comparison warning" in output and "Comparison results" in output
    assert "SKIPPED: True" in output


FAILED_CHECK_SETUP = """
import asyncio
from types import SimpleNamespace
from src.console import logger
from src.meta import Meta
from src.trackerstatus import TrackerStatusManager
from src.uphelper import UploadHelper
manager = TrackerStatusManager({})
helper = UploadHelper({'DEFAULT': {}})
meta = Meta()
"""


@pytest.mark.parametrize("answer, expected", [("yes", True), ("no", False), ("", False)])
def test_failed_check_review_includes_reason_and_keeps_answers_and_default(answer, expected):
    script = (
        FAILED_CHECK_SETUP
        + """
def check(meta):
    logger.info('[red]PRIVATEHD: This media is not registered.[/red]')
    logger.info('Add it here: https://tracker.example/add/tv')
    logger.info('Request failed: https://tracker.example/api?api_key=secret-token')
    return False
print('PROCEED:', asyncio.run(manager._run_additional_checks('PRIVATEHD', SimpleNamespace(get_additional_checks=check), meta, helper)))
print('NEXT:', cli_ui.ask_yes_no('Next question?', default=True))
"""
    )
    output, events = _run_questions(script, answer + "\nyes\n")
    prompt = events[0]["prompt"]
    assert prompt["kind"] == "yes_no"
    assert prompt["default"] is False
    assert prompt["question"] == "PRIVATEHD: one or more upload checks failed. Do you want to proceed with the upload anyway?"
    assert prompt["check_review"] == {
        "tracker": "PRIVATEHD",
        "kind": "upload",
        "messages": [
            "PRIVATEHD: This media is not registered.",
            "Add it here: https://tracker.example/add/tv",
            "Request failed: https://tracker.example/api?api_key=[REDACTED]",
        ],
    }
    assert f"PROCEED: {expected}" in output
    assert "check_review" not in events[2]["prompt"]


def test_failed_check_without_explanation_has_no_invented_reason():
    script = (
        FAILED_CHECK_SETUP
        + """
print('PROCEED:', asyncio.run(manager._run_additional_checks('TEST', SimpleNamespace(get_additional_checks=lambda meta: False), meta, helper)))
"""
    )
    _, events = _run_questions(script, "no\n")
    assert events[0]["prompt"]["check_review"]["messages"] == []


def test_rule_question_inside_check_uses_its_own_explanations():
    script = (
        FAILED_CHECK_SETUP
        + """
async def check(meta):
    logger.info('[yellow]TRACKER: Rule check returned a warning.[/yellow]')
    return await helper.prompt_yes_no('Do you want to continue anyway?', default=False)
print('PROCEED:', asyncio.run(manager._run_additional_checks('TRACKER', SimpleNamespace(get_additional_checks=check), meta, helper)))
"""
    )
    output, events = _run_questions(script, "no\nyes\n")
    opened = [event["prompt"] for event in events if event["op"] == "open"]
    assert [prompt["check_review"]["kind"] for prompt in opened] == ["rules", "upload"]
    assert all(prompt["check_review"]["messages"] == ["TRACKER: Rule check returned a warning."] for prompt in opened)
    assert opened[0]["question"] == "Do you want to continue anyway?"
    assert "PROCEED: True" in output


@pytest.mark.parametrize("answer, expected", [("yes", True), ("no", False)])
def test_failed_duplicate_search_review_keeps_redacted_exception_and_decision(answer, expected):
    script = (
        FAILED_CHECK_SETUP
        + """
print('PROCEED:', asyncio.run(manager._confirm_failed_dupe_check('TEST', RuntimeError('Request failed: https://tracker.example/api?token=private-token'), helper)))
"""
    )
    output, events = _run_questions(script, answer + "\n")
    prompt = events[0]["prompt"]
    assert prompt["question"] == "Duplicate check failed on TEST. Do you want to proceed with the upload anyway?"
    assert prompt["default"] is False
    assert prompt["check_review"] == {"tracker": "TEST", "kind": "duplicate", "messages": ["Request failed: https://tracker.example/api?token=[REDACTED]"]}
    assert f"PROCEED: {expected}" in output


def test_check_review_console_only_keeps_original_question_and_logging():
    script = (
        FAILED_CHECK_SETUP
        + """
def check(meta):
    logger.info('[red]TEST: Missing media ID[/red]')
    return False
print('PROCEED:', asyncio.run(manager._run_additional_checks('TEST', SimpleNamespace(get_additional_checks=check), meta, helper)))
"""
    )
    output, events = _run_questions(script, "no\n", enabled=False)
    assert "TEST: Missing media ID" in output
    assert "TEST: one or more upload checks failed." in output
    assert "PROCEED: False" in output
    assert events == []


def test_privatehd_media_registration_failure_reaches_gui_from_real_check():
    script = (
        FAILED_CHECK_SETUP
        + """
from src.trackers.AVISTAZ.privatehd import PrivateHD
tracker = PrivateHD.__new__(PrivateHD)
tracker.config = {'TRACKERS': {'PRIVATEHD': {'check_for_rules': False}}}
async def no_cookies(*args): return None
async def not_registered(*args): return False
tracker.cookie_validator = SimpleNamespace(load_session_cookies=no_cookies)
tracker.get_media_code = not_registered
meta.category = 'TV'
meta.type = 'WEBDL'
print('PROCEED:', asyncio.run(manager._run_additional_checks('PRIVATEHD', tracker, meta, helper)))
"""
    )
    output, events = _run_questions(script, "no\n")
    review = events[0]["prompt"]["check_review"]
    assert review["tracker"] == "PRIVATEHD"
    assert review["messages"] == ["PRIVATEHD: This media is not registered, please add it to the database by following this link: https://privatehd.to/add/tv"]
    assert "PROCEED: False" in output


def test_check_review_preserves_lines_after_redacted_url_and_deduplicates_messages():
    from src.webui_prompts import build_check_review

    review = build_check_review("TEST", ["[red]Request failed[/red]\nhttps://tracker.example/api?token=secret-token\nTry again later.", "Same message", "Same message", ""])
    assert review["messages"] == ["Request failed\nhttps://tracker.example/api?token=[REDACTED]\nTry again later.", "Same message"]


def test_check_review_redacts_multiline_json_response_without_losing_error():
    from src.webui_prompts import build_check_review

    message = 'Response: {\n  "token": "test-sensitive-value",\n  "error": "Invalid request"\n}\nTry again later.'
    review = build_check_review("TEST", [message])
    assert "test-sensitive-value" not in review["messages"][0]
    assert "[REDACTED]" in review["messages"][0]
    assert "Invalid request" in review["messages"][0]
    assert "Try again later." in review["messages"][0]
