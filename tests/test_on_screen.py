"""Nova does jobs on screen so the user can watch: window buttons clicked by her, a visible terminal, and hidden
PowerShell turned back when an on-screen tool can do the job."""
import pytest

from nova.actions import tools
from nova.avatar import avatar_client


@pytest.mark.parametrize("command,way", [
    ("git status", "VS Code"), ("cd repo; git log -1", "VS Code"), (r"Get-ChildItem -Path C:\x", "list_folder"),
    ("Get-Content notes.txt", "read_text_file"), ("Stop-Process -Name chrome", "close_all_windows"),
    ("Start-Process chrome", "open_app"), ("ssh -T git@github.com", "run_in_terminal"),
    ("Get-Process | Where MainWindowTitle", "list_windows"),
])
def test_on_screen_jobs_get_their_tool(command, way):
    assert way in tools.ui_way(command)


@pytest.mark.parametrize("command", ["Get-CimInstance Win32_Battery", "Get-NetAdapter", "powercfg /batteryreport",
                                     "Start-Process powershell -Verb RunAs"])
def test_system_jobs_still_run_hidden(command):
    assert tools.ui_way(command) is None


def test_powershell_is_turned_back_unless_she_says_why(monkeypatch):
    ran = []
    monkeypatch.setattr(tools.common, "_ps", lambda c, timeout=0: ran.append(c) or "ok")
    assert tools.run_powershell("git status").startswith("Not run") and not ran
    assert tools.run_powershell("git status", no_ui_way="VS Code isn't installed") == "ok" and ran == ["git status"]
    assert tools.run_powershell("git status", as_admin=False, no_ui_way="  ").startswith("Not run")


@pytest.fixture
def fake_window(monkeypatch):
    state = {"closed": False, "clicks": [], "messages": []}
    monkeypatch.setattr(avatar_client, "running", lambda: True)
    monkeypatch.setattr(tools.windows, "_find_window", lambda q: (42, "Notes - Notepad", "notepad.exe"))
    monkeypatch.setattr(tools.windows, "_caption_button", lambda h, which: (900, 10))
    monkeypatch.setattr(tools._user32, "IsIconic", lambda h: False)
    monkeypatch.setattr(tools._user32, "PostMessageW", lambda h, m, w, l: state["messages"].append(m))
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    return state


def test_close_is_a_click_on_the_x(monkeypatch, fake_window):
    def click(x, y, button="left", double=False, label=""):
        fake_window["clicks"].append((x, y, label))
        fake_window["closed"] = True
    monkeypatch.setattr(tools.forms, "_nova_click", click)
    monkeypatch.setattr(tools.windows, "_done", lambda h, a: fake_window["closed"])
    assert tools.window_action("notepad", "close") == "Closed 'Notes - Notepad'."
    assert fake_window["clicks"] == [(900, 10, "X on Notes - Notepad")] and fake_window["messages"] == []


def test_a_save_question_is_left_to_the_user(monkeypatch, fake_window):
    monkeypatch.setattr(tools.forms, "_nova_click", lambda *a, **k: None)
    monkeypatch.setattr(tools.windows, "_done", lambda h, a: False)
    monkeypatch.setattr(tools.windows, "_asks_to_save", lambda h, pid: True)
    out = tools.window_action("notepad", "close")
    assert "asking whether to save" in out and fake_window["messages"] == []     # no forced close


def test_a_click_that_missed_falls_back_to_closing_it(monkeypatch, fake_window):
    monkeypatch.setattr(tools.forms, "_nova_click", lambda *a, **k: None)
    monkeypatch.setattr(tools.windows, "_done", lambda h, a: False)
    monkeypatch.setattr(tools.windows, "_asks_to_save", lambda h, pid: False)
    assert tools.window_action("notepad", "close") == "Closed 'Notes - Notepad'."
    assert fake_window["messages"] == [0x0010]                                  # WM_CLOSE


def test_without_her_body_windows_close_the_old_way(monkeypatch, fake_window):
    monkeypatch.setattr(avatar_client, "running", lambda: False)
    monkeypatch.setattr(tools.forms, "_nova_click", lambda *a, **k: pytest.fail("clicked without a body"))
    assert tools.window_action("notepad", "close") == "Closed 'Notes - Notepad'." and fake_window["messages"] == [0x0010]


def test_terminal_checks_its_input():
    assert "no command" in tools.run_in_terminal("   ")
    assert "no folder" in tools.run_in_terminal("dir", folder=r"C:\definitely\not\here")


def test_new_tools_are_registered_and_need_an_ok():
    names = {t["name"] for t in tools.TOOLS}
    assert "run_in_terminal" in names and "run_in_terminal" in tools.RISKY
    rp = next(t for t in tools.TOOLS if t["name"] == "run_powershell")
    assert "no_ui_way" in rp["input_schema"]["properties"]


@pytest.mark.parametrize("topic", [r"last commit of my project in C:\Users\me\code", "git log of my repo",
                                   "what changed in my files"])
def test_questions_about_this_laptop_are_not_web_research(topic):
    assert tools.research_in_background(topic).startswith("Not started")


def test_the_same_research_is_not_started_twice(monkeypatch):
    from nova.actions import coworker
    monkeypatch.setattr(coworker, "jobs", [{"id": 7, "kind": "research", "status": "working", "title": "best laptops 2026"}])
    assert "already researching" in tools.research_in_background("Best laptops 2026").lower()
