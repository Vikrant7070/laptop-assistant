"""Tool arguments come from the model (and indirectly from web pages / window titles it reads), so they must never be
able to inject shell or PowerShell code. Commands are captured instead of run."""
import os
import subprocess

import pytest

from nova import shared
from nova.actions import tools

EVIL = ["x'; calc; '", 'x" & calc & "', "x$(calc)", "x`calc`", "x; Start-Process calc"]


@pytest.fixture
def ps(monkeypatch):
    seen = []
    monkeypatch.setattr(tools, "_ps", lambda cmd, timeout=30: (seen.append(cmd), "(done, no output)")[1])
    return seen


@pytest.fixture
def popen(monkeypatch):
    seen = []

    class P:
        def __init__(self, args, **kw):
            seen.append((args, kw))
    monkeypatch.setattr(subprocess, "Popen", P)
    return seen


def _single_quoted_safe(cmd, payload):
    """payload appears only inside a correctly escaped single-quoted PowerShell string."""
    return f"'{shared.ps_escape(payload)}'" in cmd


def test_ps_escape():
    assert shared.ps_escape("a'b") == "a''b"


@pytest.mark.parametrize("payload", EVIL)
def test_winget_search_injection(ps, payload):
    tools.search_installable_apps(payload)
    assert _single_quoted_safe(ps[-1], payload), ps[-1]


@pytest.mark.parametrize("payload", EVIL)
def test_winget_install_injection(ps, payload):
    tools.install_app(payload)
    assert _single_quoted_safe(ps[-1], payload), ps[-1]


@pytest.mark.parametrize("payload", EVIL)
def test_winget_uninstall_injection(ps, payload):
    tools.uninstall_app(payload)
    assert _single_quoted_safe(ps[-1], payload), ps[-1]


@pytest.mark.parametrize("payload", EVIL)
def test_list_installed_apps_injection(ps, payload):
    tools.list_installed_apps(payload)
    assert "'*" + shared.ps_escape(tools._like_literal(payload)) + "*'" in ps[-1]


def test_recycle_bin_path_injection(ps, tmp_path):
    f = tmp_path / "it's here.txt"
    f.write_text("x")
    tools.delete_to_recycle_bin(str(f))
    assert _single_quoted_safe(ps[-1], str(f)), ps[-1]


@pytest.mark.parametrize("payload", EVIL)
def test_open_app_no_shell(popen, monkeypatch, payload):
    monkeypatch.setattr(os, "startfile", lambda *a, **k: None, raising=False)
    tools.open_app(payload)
    for args, kw in popen:
        assert not kw.get("shell"), f"open_app ran through the shell: {args}"


def test_open_website_encodes_and_no_shell(popen, monkeypatch):
    monkeypatch.setattr(tools, "_browser_in_use", lambda: "chrome.exe")
    monkeypatch.setattr(tools, "_exe_path", lambda name: r"C:\Chrome\chrome.exe")
    out = tools.open_website('c++ & "rock" music')
    args, kw = popen[-1]
    assert not kw.get("shell")
    assert "c%2B%2B+%26+%22rock%22+music" in out


def test_open_website_plain_url(popen, monkeypatch):
    monkeypatch.setattr(tools, "_browser_in_use", lambda: None)
    opened = []
    monkeypatch.setattr(tools.webbrowser, "open", opened.append)
    tools.open_website("youtube.com")
    assert opened == ["https://youtube.com"]


def test_clean_temp_refuses_without_temp(monkeypatch, tmp_path):
    keep = tmp_path / "precious.txt"
    keep.write_text("x")
    os.utime(keep, (0, 0))
    monkeypatch.delenv("TEMP", raising=False)
    monkeypatch.chdir(tmp_path)
    tools.clean_temp_files()
    assert keep.exists(), "clean_temp_files deleted files from the current folder"


def test_kill_process_never_kills_nova(monkeypatch):
    killed = []

    class FakeProc:
        def __init__(self, pid, name):
            self.info = {"pid": pid, "name": name}

        def terminate(self):
            killed.append(self.info["pid"])
    me = os.getpid()
    monkeypatch.setattr(tools.psutil, "process_iter", lambda attrs=None: [FakeProc(me, "python.exe"),
                                                                       FakeProc(99999, "python.exe")])
    tools.kill_process("python")
    assert me not in killed and 99999 in killed


def test_set_autostart_quotes_paths(ps, monkeypatch, tmp_path):
    from nova.core import app
    monkeypatch.setattr(app, "STARTUP_LNK", tmp_path / "O'Brien" / "Nova.lnk")
    app.set_autostart(True)
    assert "O''Brien" in ps[-1]


def test_copy_missing_source_is_clear(tmp_path):
    out = tools.copy_path(str(tmp_path / "nope"), str(tmp_path / "dest"))
    assert "does not exist" in out


def test_list_folder_survives_unreadable_entry(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    assert "a.txt" in tools.list_folder(str(tmp_path))


def test_notes_and_reminders_roundtrip():
    assert "#1" in tools.add_note("buy milk")
    assert "buy milk" in tools.list_notes()
    tools.set_reminder("stretch", 0)
    assert tools.due_reminders() == ["stretch"]
    assert tools.due_reminders() == []


def test_ps_escape_typographic_quotes():
    assert shared.ps_escape("a’b") == "a’’b"
