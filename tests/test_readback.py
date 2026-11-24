"""4c (the whole laptop): every step that changes something is checked by reading the result back."""
import json
import os
import time

import pytest

from nova import store, task_state as ts
from nova.actions import checks, readback
from nova.core import app


@pytest.fixture
def real_checks(monkeypatch):
    """conftest makes checks.evaluate always pass: these tests need it to really look (once, no waiting)."""
    monkeypatch.setattr(checks, "evaluate", lambda c, ctx=None, timeout=0, **k: getattr(checks, c["check"])(c.get("arg", ""), ctx or {}))


@pytest.fixture
def full():
    s = app.settings()
    s["autonomy"] = "full"
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")


# --- the table ------------------------------------------------------------------------------------------------

def test_a_written_file_is_read_back(tmp_path):
    f = tmp_path / "list.txt"
    f.write_text("shopping list: milk", encoding="utf-8")
    assert readback.read_back("write_text_file", {"path": str(f), "content": "shopping list: milk"}, "")["state"] == "passed"
    assert readback.read_back("write_text_file", {"path": str(f), "content": "something else"}, "") == \
        {"state": "failed", "why": "list.txt doesn't hold what was written", "target": str(f)}
    gone = tmp_path / "multi.txt"
    assert readback.read_back("write_text_file", {"path": str(gone), "content": "x"}, "")["why"] == "multi.txt is missing"


def test_move_rename_copy_create_delete(tmp_path):
    old, new = tmp_path / "old.txt", tmp_path / "new.txt"
    old.write_text("x", encoding="utf-8")
    assert readback.read_back("move_or_rename", {"source": str(old), "destination": str(new)}, "")["state"] == "failed"
    old.rename(new)
    assert readback.read_back("move_or_rename", {"source": str(old), "destination": str(new)},
                              f"Moved {old} -> {new}")["state"] == "passed"
    assert readback.read_back("copy_path", {"source": str(new), "destination": str(tmp_path / "c.txt")}, "")["state"] == "failed"
    assert readback.read_back("create_folder", {"path": str(tmp_path / "exam-made")}, "")["why"] == "exam-made is missing"
    (tmp_path / "exam-made").mkdir()
    assert readback.read_back("create_folder", {"path": str(tmp_path / "exam-made")}, "")["state"] == "passed"
    assert readback.read_back("delete_to_recycle_bin", {"path": str(new)}, "")["why"] == "new.txt is still there"


def test_apps_windows_and_settings(desktop, real_checks):
    desktop.set_windows([(2, "Untitled - Notepad", "notepad.exe")], front=2)
    assert readback.read_back("open_app", {"name": "notepad"}, "Opened notepad.")["state"] == "passed"
    assert readback.read_back("open_app", {"name": "spotify"}, "Opened spotify.") == \
        {"state": "failed", "why": "no spotify window is open", "target": "spotify"}
    assert readback.read_back("window_action", {"title_or_app": "Notepad", "action": "focus"}, "")["state"] == "passed"
    assert readback.read_back("window_action", {"title_or_app": "Notepad", "action": "close"}, "")["state"] == "failed"
    desktop.set_dark_mode(True)
    assert readback.read_back("set_dark_mode", {"enabled": True}, "")["state"] == "passed"
    assert readback.read_back("set_dark_mode", {"enabled": False}, "")["why"] == "Windows is still dark"


def test_text_that_cant_be_seen_is_skipped_not_failed(desktop, real_checks, monkeypatch):
    monkeypatch.setattr(checks, "_page_has", lambda arg, ctx: (False, "'Notepad' doesn't show it"))
    rb = readback.read_back("type_text", {"text": "hello exam", "into_window": "notepad"}, "Typed 10 characters.")
    assert rb["state"] == "skipped" and "couldn't see the text" in rb["why"]


def test_brightness_and_wifi_are_read_again(monkeypatch):
    from nova.actions.tools import common
    monkeypatch.setattr(common, "_ps", lambda cmd: "40")
    assert readback.read_back("set_brightness", {"percent": 40}, "")["state"] == "passed"
    assert readback.read_back("set_brightness", {"percent": 80}, "")["why"] == "brightness is 40%, not 80%"
    monkeypatch.setattr(common, "_ps", lambda cmd: "Admin State    Connected\n")
    assert readback.read_back("set_brightness", {"percent": 40}, "")["state"] == "skipped"
    monkeypatch.setattr(common, "_ps", lambda cmd: "Wi-Fi\n   Administrative state: Disabled\n")
    assert readback.read_back("toggle_wifi", {"on": True}, "")["why"] == "Wi-Fi is still off"


def test_reading_steps_need_no_check_and_unknown_changes_are_skipped_with_why():
    assert readback.read_back("list_folder", {"path": "x"}, "") is None
    assert readback.read_back("get_system_status", {}, "") is None
    assert readback.read_back("press_keys", {"keys": "ctrl+s"}, "") == \
        {"state": "skipped", "why": "keys can't be read back", "target": ""}
    assert readback.read_back("run_skill", {"name": "x"}, "")["why"] == "the skill checks its own steps"
    assert readback.read_back("some_new_tool", {}, "")["why"] == "no read-back for this step yet"


def test_a_reader_that_crashes_is_skipped_never_passed(monkeypatch):
    monkeypatch.setitem(readback.READERS, "create_folder", lambda a, o: 1 / 0)
    assert readback.read_back("create_folder", {"path": "x"}, "")["state"] == "skipped"


# --- the card -------------------------------------------------------------------------------------------------

def _card_with(*checks_):
    card = ts.new("rename it", [{"text": "rename old.txt", "tool": "move_or_rename"}, {"text": "tell the user", "tool": ""}])
    for tool, state, target in checks_:
        rid = ts.start(card, {"name": tool, "args": {}})
        ts.finish(card, "ok", True, run=rid)
        ts.set_check(card, rid, {"state": state, "why": f"{target} {state}", "target": target})
    return card


def test_a_later_fix_replaces_an_earlier_failure():
    assert ts.failing(_card_with(("move_or_rename", "failed", "new.txt"))) == ["new.txt failed"]
    assert ts.failing(_card_with(("move_or_rename", "failed", "new.txt"), ("move_or_rename", "passed", "new.txt"))) == []


def test_done_not_checked_and_the_couldnt_finish_list():
    card = _card_with(("press_keys", "skipped", ""))
    ts.mark(card, "done")
    assert ts.not_checked(card) and ts.strip(card)["not_checked"] is True
    card = _card_with(("move_or_rename", "failed", "new.txt"))
    assert ts.left(card) == ["step 1: rename old.txt (new.txt failed)", "step 2: tell the user"]
    assert "NOT TRUE YET (read back by code): new.txt failed" in ts.render(card)
    assert ts.strip(card)["runs"][-1]["check"]["state"] == "failed"


# --- in a real task -------------------------------------------------------------------------------------------

def test_a_fake_save_is_caught_told_once_then_reported_plainly(brain, full, tmp_path):
    target = tmp_path / "multi.txt"
    fake, ran = brain([("", [("write_text_file", {"path": str(target), "content": "step one done"})]),
                       ("Saved it!", []), ("Saved it, really!", [])], tool_output=f"Wrote {target}")
    out, _ = app.answer(f"write step one done into {target}")
    tool_results = " ".join(str(m.get("content", "")) for m in fake.seen[1])
    assert "[check: FAILED - multi.txt is missing. It is NOT done.]" in tool_results      # the brain is told at once
    assert len(fake.seen) == 3                                                           # one more go, then stop
    assert out["reply"] == "I don't think I finished that - multi.txt is missing. Want me to try again?"
    card = app.state["task"]["card"]
    assert card["status"] == "failed" and ts.left(card) == ["multi.txt is missing"]


def test_a_real_save_passes(brain, full, tmp_path, monkeypatch):
    target = tmp_path / "list.txt"

    def run(name, args):
        target.write_text(args["content"], encoding="utf-8")
        return f"Wrote {target}"
    brain([("", [("write_text_file", {"path": str(target), "content": "milk"})]), ("Saved!", [])])
    monkeypatch.setattr(app.tools, "run_tool", run)
    out, _ = app.answer(f"write milk into {target}")
    assert out["reply"] == "Saved!" and app.state["task"]["card"]["runs"][0]["check"]["state"] == "passed"


def test_a_task_stops_at_its_time_limit_and_can_be_continued(brain, full, monkeypatch):
    monkeypatch.setattr(app.conversation, "TASK_SECS", -1)
    brain([("", [("get_datetime", {})])] * 5)
    out, _ = app.answer("do a long job")
    assert "That took over 10 minutes, so I paused here. Say “keep going” and I'll carry on." in out["reply"]
    card = app.state["task"]["card"]
    assert card["status"] == "stopped" and card["why"] == "time limit" and ts.resumable(card)


def test_a_failed_read_back_fails_its_plan_step_and_the_tasks_page_counts_plan_steps():
    from nova.core.app import tasks_page
    card = _card_with(("move_or_rename", "failed", "new.txt"), ("press_keys", "skipped", ""))
    assert card["plan"][0]["state"] == "failed" and card["plan"][0]["result"] == "new.txt failed"
    row = tasks_page.task_row(card)
    assert (row["steps_done"], row["steps_total"]) == (0, 2)


# --- what the exam on this branch found (2026-11-23) -----------------------------------------------------------

def test_nothing_created_is_a_failed_check(tmp_path):
    rb = readback.read_back("create_folder", {"path": str(tmp_path)}, f"Nothing created: {tmp_path} was already there.")
    assert rb["state"] == "failed" and "no new folder was made" in rb["why"]


def test_the_judge_checks_make_folder_and_rename_goals():
    from nova.actions import judge
    made = judge.goal_checks(r"create a folder called exam-made in C:\Temp\nova-exam-x")
    assert made == {"checks": [{"check": "file_exists", "arg": r"C:\Temp\nova-exam-x\exam-made"}], "covers": True}
    made = judge.goal_checks(r"rename C:\Temp\nova-exam-x\old.txt to new.txt")
    assert made["checks"] == [{"check": "file_exists", "arg": r"C:\Temp\nova-exam-x\new.txt"}]


def test_the_exam_counts_a_paused_task_as_not_done():
    from nova import exam
    assert exam.claimed({"reply": "(That was a lot of steps, so I paused here. Say “keep going” and I'll carry on.)"}) == "not done"


# --- 4c-4: downloads, sent mail and messages, forms, the wallpaper ---------------------------------------------

@pytest.fixture
def no_waiting(monkeypatch):
    for name in ("DOWNLOAD_WAIT", "SENT_WAIT", "FORM_WAIT", "POLL"):
        monkeypatch.setattr(readback, name, 0)


def test_a_download_that_is_still_crdownload_fails_and_a_finished_one_passes(tmp_path, no_waiting):
    started = time.time()
    part = tmp_path / "report.pdf.crdownload"
    part.write_bytes(b"half")
    rb = readback.check_download(str(tmp_path / "report.pdf"), started=started)
    assert rb["state"] == "failed" and rb["why"] == "report.pdf is still downloading (not finished)"
    assert readback.check_download(str(part), started=started)["state"] == "failed"
    part.rename(tmp_path / "report.pdf")
    rb = readback.check_download(str(tmp_path / "report.pdf"), started=started)
    assert rb["state"] == "passed" and rb["why"] == "report.pdf is downloaded (4 bytes)"


def test_a_missing_empty_or_old_download_fails(tmp_path, no_waiting):
    assert readback.check_download(str(tmp_path / "gone.zip"))["why"] == "gone.zip is missing"
    (tmp_path / "empty.zip").write_bytes(b"")
    assert readback.check_download(str(tmp_path / "empty.zip"))["why"] == "empty.zip is empty (0 bytes)"
    old = tmp_path / "old.zip"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    rb = readback.check_download(str(old), started=time.time() + 3600)      # ctime too: the step "started" later
    assert rb["state"] == "failed" and "there before this step" in rb["why"]


def test_a_download_click_looks_for_a_new_finished_file_in_downloads(desktop, tmp_path, no_waiting):
    desktop.folders = {"Downloads": str(tmp_path)}
    started = time.time()
    click = {"name": "Download PDF", "window": "Chrome"}
    assert readback.read_back("click_element", click, "Clicked", started)["why"] == f"no new file in {tmp_path.name}"
    (tmp_path / "Unconfirmed 1234.crdownload").write_bytes(b"x")
    assert readback.read_back("click_element", click, "Clicked", started)["why"] == \
        "Unconfirmed 1234.crdownload is still downloading (not finished)"
    (tmp_path / "Unconfirmed 1234.crdownload").rename(tmp_path / "invoice.pdf")
    assert readback.read_back("click_element", click, "Clicked", started)["state"] == "passed"
    desktop.folders = {}
    assert readback.read_back("click_element", click, "Clicked", started)["why"] == "couldn't find the Downloads folder"


def test_a_connectors_download_reads_the_file_it_names(tmp_path, no_waiting):
    f = tmp_path / "data.csv"
    f.write_text("a,b", encoding="utf-8")
    assert readback.read_back("mcp_drive_download_file", {"file_id": "1"}, f"Saved to {f}", time.time())["state"] == "passed"
    assert readback.read_back("mcp_drive_download_file", {"path": str(tmp_path / "no.csv")}, "", time.time())["state"] == "failed"
    assert readback.read_back("mcp_drive_list_downloads", {}, "", time.time())["why"] == "no read-back for this step yet"
    assert readback.read_back("mcp_gmail_get_sent_messages", {}, "")["why"] == "no read-back for this step yet"


@pytest.fixture
def sent_box(monkeypatch):
    """Gmail's Sent folder, faked: a search finds the mails whose subject is in the query."""
    from nova.actions import gmail
    sent, queries = [], []
    monkeypatch.setattr(gmail, "connected", lambda: True)

    def search(query="", count=5):
        queries.append(query)
        return [m for m in sent if f'subject:"{m["subject"]}"' in query][:count]
    monkeypatch.setattr(gmail, "search", search)
    return sent, queries


def test_a_sent_email_found_in_sent_passes_and_one_not_found_fails(sent_box, no_waiting):
    sent, queries = sent_box
    args = {"to": "Sam <sam@example.com>", "subject": "Lunch on Friday", "body": "See you there"}
    rb = readback.read_back("mcp_gmail_send_email", args, "Sent.", 1_800_000_000)
    assert rb == {"state": "failed", "why": "'Lunch on Friday' isn't in Gmail's Sent", "target": "sent mail"}
    assert queries[-1] == 'in:sent after:1799999940 to:sam@example.com subject:"Lunch on Friday"'
    sent.append({"subject": "Lunch on Friday"})
    rb = readback.read_back("mcp_gmail_send_email", args, "Sent.", 1_800_000_000)
    assert rb["state"] == "passed" and rb["why"] == "it is in Sent: 'Lunch on Friday'"
    assert "See you there" not in " ".join(queries)                      # the body is never searched for


def test_sent_messages_without_a_reader_are_skipped_with_why(monkeypatch, no_waiting):
    from nova.actions import gmail
    monkeypatch.setattr(gmail, "connected", lambda: False)
    assert readback.read_back("mcp_gmail_send_email", {"to": "a@b.co"}, "")["why"] == \
        "Gmail isn't connected here, so Sent can't be read back"
    assert readback.read_back("mcp_telegram_send_message", {"text": "hi"}, "")["why"] == \
        "Telegram doesn't let a bot read its chat history back"
    assert readback.read_back("mcp_phonelink_send_sms", {"text": "hi"}, "")["state"] == "skipped"
    assert readback.read_back("mcp_slack_post_message", {"text": "hi"}, "")["why"] == "no read-back for this step yet"
    assert readback.read_back("mcp_outlook_send_mail", {"to": "a@b.co"}, "")["why"] == \
        "this connector has no way to read what it sent"


def test_a_form_with_no_confirmation_text_fails(real_checks, monkeypatch, no_waiting):
    page = {"text": "Contact us. Name Email Message Submit"}
    looked = []

    def page_has(arg, ctx):
        looked.append(arg)
        return arg in page["text"].lower(), "the page"
    monkeypatch.setattr(checks, "_page_has", page_has)
    submit = {"name": "Submit", "window": "Contact - Chrome"}
    rb = readback.read_back("click_element", submit, "Clicked 'Submit'.")
    assert rb["state"] == "failed" and rb["why"].startswith("no confirmation on the page after clicking 'Submit'")
    page["text"] = "Thank you! We'll be in touch."
    assert readback.read_back("click_element", submit, "Clicked 'Submit'.")["why"] == \
        "the page says 'thank you' after clicking 'Submit'"
    assert set(looked) <= set(readback.CONFIRMED)                     # only the confirmation words, never a value
    monkeypatch.setattr(checks, "_page_has", lambda arg, ctx: (False, "no window"))
    assert readback.read_back("click_element", submit, "")["why"] == "couldn't see the page after the click"


def test_fill_form_and_other_clicks_are_skipped_with_why():
    rb = readback.read_back("fill_form", {"window": "Chrome"}, "Filled 3 box(es). I did NOT submit it.")
    assert rb["state"] == "skipped" and "never submits" in rb["why"] and "private" in rb["why"]
    assert readback.read_back("click_element", {"name": "Next page"}, "")["why"].startswith("no read-back for this click")
    assert readback.read_back("click_at", {"x": 1, "y": 2}, "")["state"] == "skipped"
    assert readback.read_back("draft_email", {"about": "x"}, "")["state"] == "skipped"


def test_the_wallpaper_is_read_back(desktop, tmp_path):
    pic = tmp_path / "sea.jpg"
    desktop.wall = str(tmp_path / "old.jpg")
    assert readback.read_back("set_wallpaper", {"image_path": str(pic)}, "")["why"] == "the wallpaper is still old.jpg"
    desktop.wall = str(pic)
    assert readback.read_back("set_wallpaper", {"image_path": str(pic)}, "")["state"] == "passed"
    desktop.wall = None
    assert readback.read_back("set_wallpaper", {"image_path": str(pic)}, "")["state"] == "skipped"


def test_a_step_in_a_task_is_read_back_with_its_start_time(brain, full, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(readback, "read_back", lambda tool, args, out, started=None: seen.append(started))
    before = time.time()
    brain([("", [("create_folder", {"path": str(tmp_path / "x")})]), ("Made it.", [])])
    app.answer(f"make a folder x in {tmp_path}")
    assert seen and before <= seen[0] <= time.time()
