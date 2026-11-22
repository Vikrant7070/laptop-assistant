"""🆘 Trouble on screen: error boxes and frozen apps are offered once; yes / no / buttons; Nova's own windows ignored."""
import ctypes
import subprocess
import sys
import time

import pytest

from nova import intents
from nova.actions import watcher
from nova.core import app


class Screen:
    def __init__(self):
        self.wins, self.texts, self.hung, self.now = [], {}, set(), 1000.0

    def watcher(self):
        return watcher.Watcher(windows=lambda: list(self.wins), read=lambda h: self.texts.get(h, ""),
                               hung=lambda h: h in self.hung, clock=lambda: self.now)


@pytest.fixture
def scr():
    return Screen()


def test_new_error_box_is_offered_once(scr):
    w = scr.watcher()
    scr.wins = [(1, "Notepad", "notes.txt - Notepad", 10, "notepad.exe")]
    assert w.scan() == []                                         # first look: just learns what's open
    scr.wins.append((2, "#32770", "Notepad", 10, "notepad.exe"))
    scr.texts[2] = "Cannot find the C:\\x.txt file."
    found = w.scan()
    assert [(f["kind"], f["app"], f["text"]) for f in found] == [("error", "Notepad", "Cannot find the C:\\x.txt file.")]
    assert w.scan() == []                                         # not again


def test_ordinary_dialogs_and_save_prompts_are_ignored(scr):
    w = scr.watcher()
    w.scan()
    scr.wins = [(3, "#32770", "Notepad", 10, "notepad.exe"), (4, "#32770", "Word", 11, "winword.exe"),
                (5, "#32770", "Nova", 12, "pythonw.exe")]
    scr.texts = {3: "Do you want to save changes to notes.txt?", 4: "Your file was printed.",
                 5: "Error: Nova's own box"}
    assert w.scan() == []


def test_dialogs_open_at_start_are_not_offered(scr):
    scr.wins = [(6, "#32770", "Setup", 10, "setup.exe")]
    scr.texts[6] = "Installation failed."
    w = scr.watcher()
    assert w.scan() == [] and w.scan() == []


def test_frozen_app_needs_ten_seconds(scr):
    w = scr.watcher()
    scr.wins = [(7, "Chrome_WidgetWin_1", "YouTube - Chrome", 20, "chrome.exe"),
                (8, "Shell_TrayWnd", "Taskbar", 21, "explorer.exe")]
    w.scan()
    scr.hung = {7, 8}
    assert w.scan() == []
    scr.now += 5
    assert w.scan() == []
    scr.now += 6
    found = w.scan()
    assert [(f["kind"], f["app"]) for f in found] == [("hung", "Chrome")]       # explorer is never offered
    scr.now += 30
    assert w.scan() == []                                         # offered once
    scr.hung = set()
    w.scan()                                                      # it recovered...
    scr.hung = {7}
    w.scan()
    scr.now += 11
    assert len(w.scan()) == 1                                     # ...and froze again: a new offer


def test_offer_lines():
    e = {"kind": "error", "app": "Excel", "text": "x" * 200, "proc": "excel.exe", "title": "Microsoft Excel"}
    line, spoken = watcher.offer_line(e)
    assert line.startswith("Excel shows an error: “" + "x" * 137 + "...”") and "help" in spoken
    assert "isn't responding" in watcher.offer_line({**e, "kind": "hung"})[0]
    assert "Excel" in watcher.help_request(e) and "x" * 200 in watcher.help_request(e)
    # live test: "appeared on my screen" made her take an 80 s screenshot first - the text is enough
    assert not intents.SCREEN.search(watcher.help_request({**e, "text": "Access is denied."}))


# ------------------------------------------------------------------ in Nova

def err(text="Access is denied."):
    return {"kind": "error", "hwnd": 2, "pid": 10, "proc": "notepad.exe", "title": "Notepad", "text": text, "app": "Notepad"}


@pytest.fixture(autouse=True)
def no_offer():
    app.help_offer.update(item=None, until=0.0, at=0.0)
    yield
    app.help_offer.update(item=None, until=0.0, at=0.0)


def test_yes_sends_the_error_to_her_brain(brain, client):
    fake, _ = brain([("It means Notepad can't write there. Save it in Documents instead.", [])])
    app.offer_help(err())
    ev = app.events[-1]
    assert ev["kind"] == "suggestion" and ev["offer"]["help"] is True and "Access is denied" in ev["reply"]
    assert "help" in app.mouth.said[-1]
    r = client.post("/api/chat", json={"text": "yes please"})
    j = r.get_json()
    assert "Documents" in j["reply"]
    assert any("Access is denied." in str(m.get("content")) for m in fake.seen[0] if m["role"] == "user")  # only now
    assert app.help_offer["item"] is None


def test_no_and_unrelated_messages(brain, client):
    brain([("Sure, it's 5 pm.", [])])
    app.offer_help(err())
    r = client.post("/api/chat", json={"text": "what time is it right now?"})   # not about the offer
    assert r.get_json()["reply"] == "Sure, it's 5 pm." and app.help_offer["item"] is not None
    r = client.post("/api/chat", json={"text": "no"})
    assert r.get_json()["reply"].startswith("Okay, I'll leave it") and app.help_offer["item"] is None


def test_buttons(brain, client):
    brain([("Here's how to fix it.", [])])
    app.offer_help(err())
    oid = app.help_offer["id"]
    assert client.post("/api/help", json={"yes": True, "id": oid + 5}).get_json()["reply"] == "That one isn't waiting any more."
    j = client.post("/api/help", json={"yes": True, "id": oid}).get_json()
    assert j["reply"] == "Here's how to fix it."


def test_offer_expires():
    app.offer_help(err())
    app.help_offer["until"] = time.time() - 1
    assert app.take_help("yes") is None


def test_close_frozen_app_only_if_still_frozen(monkeypatch):
    killed = []

    class P:
        def __init__(self, pid): self.pid = pid
        def name(self): return "chrome.exe"
        def kill(self): killed.append(self.pid)
    monkeypatch.setattr(watcher.psutil, "Process", P)
    item = {"kind": "hung", "hwnd": 7, "pid": 20, "proc": "chrome.exe", "title": "YouTube", "text": "", "app": "Chrome"}
    monkeypatch.setattr(watcher, "is_hung", lambda h: False)
    assert "responding again" in watcher.close_hung(item) and killed == []
    monkeypatch.setattr(watcher, "is_hung", lambda h: True)
    app.offer_help(item)
    out = app.take_help("close it")
    assert killed == [20] and out["reply"].startswith("Closed Chrome")


def test_never_closes_windows_itself(monkeypatch):
    class P:
        def __init__(self, pid): pass
        def name(self): return "explorer.exe"
        def kill(self): raise AssertionError("killed explorer")
    monkeypatch.setattr(watcher.psutil, "Process", P)
    monkeypatch.setattr(watcher, "is_hung", lambda h: True)
    assert "won't close" in watcher.close_hung({"pid": 1, "hwnd": 1, "app": "Explorer"})


def test_job_offer_newer_than_help_wins(brain, client):
    app.offer_help(err())
    app.help_offer["at"] = time.time() - 100
    job = {"id": 1, "kind": "email", "title": "Hi", "status": "done", "result": "ok"}
    app.offer.update(job=job, until=time.time() + app.OFFER_SECS)
    assert app.help_first() is False


# ------------------------------------------------------------------ a real Windows error box

def test_reads_a_real_message_box():
    """Opens a real (classic) Windows message box in a child process and checks the watcher reads its text."""
    code = ("import ctypes; ctypes.windll.user32.MessageBoxW(0, 'Cannot open file C:/nope.txt. Access is denied.', "
            "'Nova test error', 0x10)")
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        hwnd = 0
        end = time.time() + 10
        while time.time() < end and not hwnd:
            hwnd = ctypes.windll.user32.FindWindowW("#32770", "Nova test error")
            time.sleep(0.1)
        assert hwnd, "the message box didn't appear"
        # The dialog window exists a moment before the Static child that holds its message (CI, 2026-11-21: read
        # too early, it came back ''), so wait for the text too, as the watcher's own repeated scans do.
        text = ""
        while time.time() < end and "Access is denied" not in text:
            text = watcher.dialog_text(hwnd)
            time.sleep(0.1)
        assert "Access is denied" in text
        w = watcher.Watcher()
        w.known = set()                                   # pretend it opened after Nova started
        w.seen = set()
        import nova.actions.watcher as wm
        old = wm.NOVA_PROCS
        wm.NOVA_PROCS = set()                             # the test box belongs to python.exe, like Nova
        try:
            found = [f for f in w.scan() if f["hwnd"] == hwnd]
        finally:
            wm.NOVA_PROCS = old
        assert found and found[0]["kind"] == "error" and "Access is denied" in found[0]["text"]
    finally:
        proc.kill()


def test_notices_a_really_frozen_window(monkeypatch):
    """A child process opens a window and then stops answering Windows' messages - like a frozen app."""
    code = ("import tkinter as tk, time; r = tk.Tk(); r.title('Nova hung test'); r.geometry('200x80+0+0'); "
            "r.update(); r.update(); time.sleep(90)")
    proc = subprocess.Popen([sys.executable, "-c", code])
    monkeypatch.setattr(watcher, "HUNG_SECS", 1)
    monkeypatch.setattr(watcher, "NOVA_PROCS", set())     # the test window belongs to python.exe, like Nova
    monkeypatch.setattr(watcher, "NEVER_CLOSE", {"explorer.exe"})
    try:
        hwnd, end = 0, time.time() + 30                   # generous waits: a busy laptop starts Python slowly
        while time.time() < end and not hwnd:
            hwnd = ctypes.windll.user32.FindWindowW(None, "Nova hung test")
            time.sleep(0.1)
        assert hwnd, "the test window didn't appear"
        w = watcher.Watcher()
        w.scan()
        end, found = time.time() + 45, []
        while time.time() < end and not found:            # Windows calls a window hung after ~5 s of silence
            ctypes.windll.user32.PostMessageW(hwnd, 0, 0, 0)   # WM_NULL: a message it never reads, so it counts as hung
            found = [f for f in w.scan() if f["hwnd"] == hwnd]
            time.sleep(0.5)
        assert found and found[0]["kind"] == "hung"
        # python.exe may start the real interpreter as a child, so the window's owner is the child or the process
        # itself. Ask psutil about OUR process (alive, we hold its handle) instead of looking the window's pid up
        # afterwards: a process that has exited by then made psutil raise NoSuchProcess (flaky on CI).
        try:
            family = {proc.pid} | {c.pid for c in watcher.psutil.Process(proc.pid).children(recursive=True)}
        except watcher.psutil.NoSuchProcess:
            family = {proc.pid}
        assert found[0]["pid"] in family
        assert watcher.close_hung(found[0]).startswith("Closed")
        proc.wait(15)
        assert proc.poll() is not None                     # it really was ended
    finally:
        if proc.poll() is None:
            proc.kill()


def test_trouble_found_during_the_cooldown_waits_its_turn(monkeypatch):
    """Live test on the running Nova: a frozen app found a minute after an error-box offer was thrown away."""
    hung = {"kind": "hung", "hwnd": 7, "pid": 20, "proc": "chrome.exe", "title": "YouTube", "text": "", "app": "Chrome"}
    batches = [[hung], [], []]
    monkeypatch.setattr(app.screen_watch, "scan", lambda: batches.pop(0) if batches else [])
    alive = {"yes": True}
    monkeypatch.setattr(watcher, "still_there", lambda i: alive["yes"])
    app.help_queue.clear()
    app.help_offer["at"] = time.time()                  # an offer was just made
    app.help_step()
    assert app.help_offer["item"] is None and len(app.help_queue) == 1
    app.help_offer["at"] = time.time() - app.HELP_EVERY  # the cooldown is over
    app.help_step()
    assert app.help_offer["item"] is hung and app.help_queue == []
    # ...but if it recovered meanwhile, it's not offered
    app.help_offer.update(item=None, at=time.time())
    batches[:] = [[dict(hung, hwnd=8)]]
    app.help_step()
    alive["yes"] = False
    app.help_offer["at"] = 0
    app.help_step()
    assert app.help_offer["item"] is None and app.help_queue == []
