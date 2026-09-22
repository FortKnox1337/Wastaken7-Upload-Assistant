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
