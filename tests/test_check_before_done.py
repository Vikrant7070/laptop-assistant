"""L3 of docs/plans/local-first-v2.md: check before "done". Text typed into an editor and "file not found" are read
back; a read-back that still fails after the one more go climbs to the cloud (local first); skipped-only doesn't."""
import json

import pytest

from nova import store, task_state as ts
from nova.actions import readback
from nova.actions.tools import common
from nova.brain import cloud, retrieval, router
from nova.core import app

CLOUD = "cloud:google:gemini-3.8-flash"
USERS = "the user's own clipboard"


@pytest.fixture
def unseen(monkeypatch):
    """The screen reader can't see the typed text (conftest's checks always pass), so the editor reader runs."""
    monkeypatch.setattr(readback, "_check", lambda *a, **k: (False, "doesn't show it"))
    monkeypatch.setattr(readback, "COPY_WAIT", 0)
    monkeypatch.setattr(readback, "SAVE_WAIT", 0)


def notepad(desktop, title="Untitled - Notepad", holds=None, saves_to=None, proc="Notepad.exe"):
    """A pretend Notepad in front: Ctrl+C copies `holds` (None: copies nothing), Ctrl+S writes `holds` to `saves_to`."""
    desktop.set_windows([(5, title, proc), (9, "Some video - Chrome", "chrome.exe")], front=5)
    desktop.clipboard = USERS

    def press(keys):
        desktop.log.append(("press", keys))
        if keys == "ctrl+c" and holds is not None:
            desktop.clipboard, desktop.clip_seq = holds, desktop.clip_seq + 1
        if keys == "ctrl+s" and saves_to is not None and holds is not None:
            saves_to.write_text(holds, encoding="utf-8")
    desktop.press = press
    return desktop


# --- type_text into an editor ----------------------------------------------------------------------------------

def test_untitled_notepad_is_copied_privately_and_the_clipboard_put_back(desktop, unseen):
    notepad(desktop, holds="Hello   exam\n")
    rb = readback.read_back("type_text", {"text": "hello exam", "into_window": "notepad"}, "Typed it.")
    assert rb == {"state": "passed", "why": "Notepad holds the typed text", "target": "Untitled - Notepad"}
    assert desktop.logged("press") == ["ctrl+a", "ctrl+c", "ctrl+end"]
    assert desktop.clipboard_text() == "" and desktop.clipboard_save() == [(13, (USERS + "\0").encode("utf-16-le"))]
    assert desktop.logged("copy") == []                 # nothing went through the user's clipboard history


def test_untitled_notepad_without_the_text_fails(desktop, unseen):
    notepad(desktop, holds="something else")
    rb = readback.read_back("type_text", {"text": "hello exam"}, "Typed it.")
    assert rb["state"] == "failed" and rb["why"] == "Notepad doesn't hold the typed text"
    assert "something else" not in json.dumps(rb)       # the copied text itself is never in the verdict


def test_a_copy_that_never_comes_or_a_busy_clipboard_is_skipped(desktop, unseen):
    notepad(desktop, holds=None)
    rb = readback.read_back("type_text", {"text": "hello exam"}, "Typed it.")
    assert rb["state"] == "skipped" and "couldn't copy Notepad's text" in rb["why"]
    assert desktop.clipboard_save() == [(13, (USERS + "\0").encode("utf-16-le"))]
    notepad(desktop, holds="hello exam")
    desktop.log.clear()
    desktop.clipboard_save = lambda: None
    assert readback.read_back("type_text", {"text": "hello exam"}, "")["state"] == "skipped"
    assert "ctrl+a" not in desktop.logged("press")      # nothing is copied over a clipboard that can't be put back


def test_a_saved_file_is_saved_and_read_from_disk(desktop, unseen, monkeypatch, tmp_path):
    monkeypatch.setattr(common, "HOME", tmp_path)
    (tmp_path / "Documents").mkdir()
    f = tmp_path / "Documents" / "notes.txt"
    f.write_text("old line", encoding="utf-8")
    notepad(desktop, title="*notes.txt - Notepad", holds="old line\nhello exam", saves_to=f)
    rb = readback.read_back("type_text", {"text": "hello exam", "into_window": "notes"}, "Typed it.")
    assert rb == {"state": "passed", "why": "notes.txt holds the typed text (saved and read back)", "target": str(f)}
    assert desktop.logged("press") == ["ctrl+s"] and desktop.logged("clip") == []   # the file, not the clipboard

    f.write_text("old line", encoding="utf-8")
    notepad(desktop, title="notes.txt - Notepad", holds=None, saves_to=f)            # Ctrl+S didn't take
    rb = readback.read_back("type_text", {"text": "hello exam"}, "Typed it.")
    assert rb["state"] == "failed" and rb["why"] == "notes.txt doesn't hold the typed text after saving it"


def test_keys_never_go_to_another_window(desktop, unseen):
    notepad(desktop, holds="hello exam")
    desktop.front = 9                                   # Chrome in front, and focusing Notepad doesn't work
    desktop.focus = lambda hwnd: desktop.log.append(("focus", hwnd))
    rb = readback.read_back("type_text", {"text": "hello exam", "into_window": "notepad"}, "")
    assert rb["state"] == "skipped" and "couldn't bring Notepad to the front" in rb["why"]
    assert desktop.logged("press") == []


def test_not_an_editor_stays_skipped(desktop, unseen):
    desktop.set_windows([(9, "Some video - Chrome", "chrome.exe")], front=9)
    rb = readback.read_back("type_text", {"text": "hello exam", "into_window": "chrome"}, "")
    assert rb["state"] == "skipped" and rb["why"] == "couldn't see the text in chrome"
    assert desktop.logged("press") == []


def test_seen_on_screen_needs_no_editor_read(desktop):
    notepad(desktop, holds="hello exam")
    assert readback.read_back("type_text", {"text": "hello exam"}, "")["state"] == "passed"
    assert desktop.logged("press") == []


# --- "file not found" ------------------------------------------------------------------------------------------

def test_file_not_found_is_a_failed_check_for_a_reading_step_too(tmp_path):
    gone = tmp_path / "does-not-exist.txt"
    rb = readback.read_back("read_text_file", {"path": str(gone)}, f"{gone} is not a file.")
    assert rb == {"state": "failed", "why": "does-not-exist.txt doesn't exist", "target": str(gone)}
    assert readback.missing("open_file", {}, "Error: the system cannot find the file specified")["state"] == "failed"
    assert readback.missing("anything", {}, "File not found – couldn’t find it")["state"] == "failed"


def test_not_found_words_about_something_else_pass_by(tmp_path):
    there = tmp_path / "notes.txt"
    there.write_text("TODO: error page not found", encoding="utf-8")
    assert readback.read_back("read_text_file", {"path": str(there)}, there.read_text()) is None   # it is there
    assert readback.missing("read_page", {}, "Welcome! " + "x" * 400 + " page not found") is None   # long text
    assert readback.missing("read_text_file", {"path": str(there)}, "Hello there") is None
    assert readback.read_back("get_datetime", {}, "Monday 10:00") is None


def test_an_error_step_that_says_not_found_is_a_failed_check(brain, full, tmp_path):
    gone = tmp_path / "report.txt"
    brain([("", [("move_or_rename", {"source": str(gone), "destination": str(tmp_path / "b.txt")})]),
           ("Renamed it!", []), ("Done, renamed!", [])], tool_output=f"Error: {gone} not found")
    out, _ = app.answer(f"rename {gone} to b.txt")
    assert out["reply"] == "I don't think I finished that - report.txt doesn't exist. Want me to try again?"
    assert app.state["task"]["card"]["runs"][0]["check"]["state"] == "failed"


def test_a_wrong_path_first_then_the_right_one_is_done(brain, tmp_path, monkeypatch):
    wrong, right = tmp_path / "a" / "notes.txt", tmp_path / "notes.txt"
    right.write_text("milk, eggs", encoding="utf-8")
    brain([("", [("read_text_file", {"path": str(wrong)})]), ("", [("read_text_file", {"path": str(right)})]),
           ("It says milk, eggs.", [])])
    monkeypatch.setattr(app.tools, "run_tool", lambda n, a: f"{a['path']} is not a file." if a["path"] == str(wrong)
                        else "milk, eggs")
    out, _ = app.answer("read me notes.txt")
    assert out["reply"] == "It says milk, eggs."
    check = app.state["task"]["card"]["runs"][0]["check"]
    assert check["state"] == "skipped" and check["why"] == "notes.txt doesn't exist (a later try worked)"


# --- when a check climbs (local first) -------------------------------------------------------------------------

@pytest.fixture
def full():
    s = app.settings()
    s["autonomy"] = "full"
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")


@pytest.fixture
def local_first(monkeypatch):
    monkeypatch.setattr(retrieval, "ready", lambda: False)
    monkeypatch.setattr(router, "installed", lambda m: True)
    cloud.set_key("google", "test-key")
    app.save_settings({**app.settings(), "provider": "google", "cloud_models": {"google": "gemini-3.8-flash"},
                       "brain_mode": "local_first", "autonomy": "full"})
    yield
    cloud.set_key("google", "")


def recorded(brain, script, tool_output="ok"):
    fake, ran = brain(script, tool_output)
    models = []

    def call(model, messages, tools_, **kw):
        models.append(model)
        return fake(model, messages, tools_, **kw)
    app.llm.chat = call
    return models


def log():
    return store.current().task_log.read_text(encoding="utf-8")


def test_a_read_back_that_still_fails_after_one_more_go_climbs(brain, local_first, tmp_path):
    target = tmp_path / "multi.txt"
    models = recorded(brain, [("", [("write_text_file", {"path": str(target), "content": "step one"})]),
                              ("Saved it!", []), ("Saved it, really!", []), ("It still isn't there, sorry.", [])],
                      tool_output=f"Wrote {target}")
    out, _ = app.answer(f"write step one into {target}")
    local = app.settings()["model"]
    assert models == [local, local, local, CLOUD]        # the step, her "done", the one more go, then the cloud
    assert "CLIMB -> cloud:google:gemini-3.8-flash: my checks still say it isn't done" in log()
    assert out["reply"] == "I don't think I finished that - multi.txt is missing. Want me to try again?"
    assert log().count("CLIMB ->") == 1


def test_a_missing_file_climbs_once_then_says_so_plainly(brain, local_first, tmp_path):
    gone = tmp_path / "does-not-exist.txt"
    models = recorded(brain, [("", [("read_text_file", {"path": str(gone)})]), ("Here it is!", []),
                              ("Here it is, really.", []), ("That file isn't there.", [])],
                      tool_output=f"{gone} is not a file.")
    out, _ = app.answer(f"read me the file {gone}")
    assert models[-1] == CLOUD and log().count("CLIMB ->") == 1
    assert out["reply"] == "I don't think I finished that - does-not-exist.txt doesn't exist. Want me to try again?"


def test_only_skipped_checks_say_done_not_checked_and_never_climb(brain, local_first):
    models = recorded(brain, [("", [("press_keys", {"keys": "ctrl+s"})]), ("Saved!", [])])
    out, _ = app.answer("press ctrl s for me")
    assert models == [app.settings()["model"]] * 2 and "CLIMB" not in log()
    card = app.state["task"]["card"]
    assert out["reply"] == "Saved!" and ts.strip(card)["not_checked"] is True


def test_plain_chat_never_climbs_even_when_it_says_it_cant(brain, local_first):
    models = recorded(brain, [("I can't taste food, and I couldn't say which is best.", [])])
    app.answer("which pizza is the best")
    assert models == [app.settings()["model"]] and "CLIMB" not in log()


# --- the screen reader's view of a short document (task exam 2026-11-24) ---------------------------------------

class _Ctrl:
    def __init__(self, kind, name="", text=None):
        self.ControlTypeName, self.Name, self._text = kind, name, text

    def Exists(self, *a):
        return self._text is not None

    def GetTextPattern(self):
        doc = self

        class Range:
            def GetText(self, limit):
                return doc._text[:limit]

        class Pattern:
            DocumentRange = Range()
        return Pattern()


def test_page_text_keeps_a_short_documents_own_text(monkeypatch):
    """Notepad's document control is named "Text editor"; a document under 80 characters used to be replaced by the
    names of the controls around it, so page_has "hello exam" said no while the text was there."""
    import sys
    import types
    from nova.actions.tools import apps
    doc = _Ctrl("DocumentControl", "Text editor", "step one done\r\nhello exam")
    walk = [(_Ctrl("TabItemControl", "*multi.txt"), 1), (doc, 2), (_Ctrl("TextControl", "Ln 2, Col 11"), 2)]
    window = types.SimpleNamespace(ControlTypeName="WindowControl", DocumentControl=lambda searchDepth=0: doc)
    fake = types.SimpleNamespace(ControlFromHandle=lambda h: window, WalkControl=lambda root, maxDepth=0: iter(walk))
    monkeypatch.setitem(sys.modules, "uiautomation", fake)
    text = apps.page_text(5)
    assert "step one done hello exam" in text and "*multi.txt" in text
    doc._text = ""                                       # an empty document: only the control names, as before
    assert "hello exam" not in apps.page_text(5) and "*multi.txt" in apps.page_text(5)
