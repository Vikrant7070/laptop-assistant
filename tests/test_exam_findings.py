"""Fixes for what the task exam found on 2026-11-23 (before 56% / after 60% success, 28% false "done")."""
import pytest

from nova import exam
from nova.actions import checks, judge, tools
from nova.actions.tools import apps, files, windows

CMD, NOTEPAD = (1, "Command Prompt", "cmd.exe"), (2, "Untitled - Notepad", "notepad.exe")


# --- the exam read "can’t" (curly apostrophe) as a claim of success ---------------------------------------------

@pytest.mark.parametrize("reply", ["You can’t start a car’s engine from here — I’m inside your laptop.",
                                   "Zorblax Studio isn’t installed here. Want me to install it?"])
def test_the_exam_reads_curly_apostrophes(reply):
    assert exam.claimed({"reply": reply}) == "not done"


def test_a_real_done_is_still_done():
    assert exam.claimed({"reply": "Done, boss! Notepad is open."}) == "done"


# --- open_app said "Opened calculator." when the Calculator app isn't installed --------------------------------

def test_open_app_says_when_the_app_behind_a_launcher_stub_is_missing(monkeypatch):
    monkeypatch.setattr(apps, "_protocol_registered", lambda name: False)
    started = []
    monkeypatch.setattr(apps.subprocess, "Popen", lambda *a, **k: started.append(a))
    out = apps.open_app("calculator")
    assert out.startswith("Couldn't open it: the Windows Calculator app isn't installed") and started == []


def test_open_app_still_opens_the_calculator_when_it_is_installed(monkeypatch):
    monkeypatch.setattr(apps, "_protocol_registered", lambda name: True)
    monkeypatch.setattr(apps.shutil, "which", lambda t: r"C:\Windows\System32\calc.exe")
    started = []
    monkeypatch.setattr(apps.subprocess, "Popen", lambda args, **k: started.append(args))
    assert apps.open_app("calculator") == "Opened calculator." and started


# --- create_folder said "Created" for a folder that was already there -------------------------------------------

def test_create_folder_says_when_nothing_was_created(tmp_path):
    assert files.create_folder(str(tmp_path)).startswith("Nothing created:")
    assert files.create_folder(str(tmp_path / "exam-made")) == f"Created {tmp_path / 'exam-made'}"
    assert (tmp_path / "exam-made").is_dir()


# --- keys into a terminal ran a shell command without the always-ask approval ----------------------------------

def test_typing_into_a_named_terminal_is_refused(desktop):
    desktop.set_windows([NOTEPAD, CMD], front=2)
    assert windows.type_text(r"ren C:\x\old.txt new.txt", into_window="Command Prompt") == windows.NO_TERMINAL_KEYS
    assert not [e for e in desktop.log if e[0] in ("write", "clip", "focus")]


@pytest.mark.parametrize("front", [CMD, (3, "Windows PowerShell", "powershell.exe"), (4, "Nova terminal", "python.exe"),
                                   (5, "C:\\WINDOWS\\system32\\cmd.exe", "WindowsTerminal.exe")])
def test_keys_never_go_into_a_terminal_in_front(desktop, front):
    desktop.set_windows([front, NOTEPAD], front=front[0])
    assert windows.type_text("del *.*") == windows.NO_TERMINAL_KEYS
    assert windows.press_keys("enter") == windows.NO_TERMINAL_KEYS
    assert not [e for e in desktop.log if e[0] in ("write", "clip", "press")]


def test_keys_still_work_in_an_ordinary_app(desktop):
    desktop.set_windows([NOTEPAD, CMD], front=2)
    assert windows.press_keys("ctrl+s") == "Pressed ctrl+s."
    assert ("press", "ctrl+s") in desktop.log


def test_the_refusal_points_at_the_tool_that_asks_first():
    assert "run_in_terminal" in windows.NO_TERMINAL_KEYS and "move_or_rename" in windows.NO_TERMINAL_KEYS
    assert "run_in_terminal" in tools.FUNCS                     # the tools it names exist
    assert "move_or_rename" in tools.FUNCS


# --- "save it as <path>" had no check, so a save that never happened was "done" ---------------------------------

def test_save_as_a_path_gets_a_file_check():
    made = judge.goal_checks(r"open Notepad, type 'step one done', and save it as C:\Temp\nova-exam\multi.txt")
    assert {"check": "file_exists", "arg": r"C:\Temp\nova-exam\multi.txt"} in made["checks"]
    assert {"check": "window_open", "arg": "notepad"} in made["checks"] and made["covers"] is False


def test_the_file_check_looks_at_the_disk(tmp_path):
    f = tmp_path / "multi.txt"
    assert "file_exists" in checks.NAMES and "file_exists" in checks.NEEDS_ARG   # (conftest stubs checks.evaluate)
    assert checks.file_exists(str(f), {})[0] is False
    f.write_text("step one done", encoding="utf-8")
    ok, seen = checks.file_exists(f'"{f}"', {})
    assert ok and "exists" in seen
