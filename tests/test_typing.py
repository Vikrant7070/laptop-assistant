"""type_text puts text in with one paste (not letter-by-letter keys, which Windows 11 Notepad garbled into
"hello eeeee") and gives the user's clipboard back. No real keys are pressed and the real clipboard is never
touched: the test runs on nova.platform's FakeDesktop, and the low-level clipboard code is tested with a fake user32."""
import pytest

from nova.actions import tools
from nova.platform import win32

USERS_CLIP = [(13, "user's own text\0".encode("utf-16-le"))]


@pytest.fixture
def desk(desktop, monkeypatch):
    """The pretend desktop with the user's own text on the clipboard; desk.logged("clip"/"press"/"write"/"restore")."""
    monkeypatch.setattr(tools.forms, "_focused_is_password", lambda: False)
    monkeypatch.setattr(tools.windows, "_point_at_focus", lambda label: False)
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    desktop.clipboard = list(USERS_CLIP)
    return desktop


def test_type_text_pastes_once_and_gives_the_clipboard_back(desk):
    assert tools.type_text("hello judge") == "Typed 11 characters."
    assert desk.logged("clip") == ["hello judge"]
    assert desk.logged("press") == ["ctrl+v"] and desk.logged("write") == []      # one paste, no letter-by-letter keys
    assert desk.logged("restore") == [(USERS_CLIP, 1)] and desk.clipboard == USERS_CLIP


def test_a_longer_sentence_with_new_lines_goes_in_whole(desk):
    text = "The quick brown fox jumps over the lazy dog.\nSecond line: 100% done, café ✓"
    tools.type_text(text)
    assert desk.logged("clip") == [text] and desk.logged("press") == ["ctrl+v"]


def test_the_clipboard_comes_back_even_if_pasting_fails(desk, monkeypatch):
    def boom(keys):
        raise RuntimeError("keyboard hook gone")
    monkeypatch.setattr(desk, "press", boom)
    with pytest.raises(RuntimeError):
        tools.type_text("hello")
    assert len(desk.logged("restore")) == 1 and desk.clipboard == USERS_CLIP


def test_busy_clipboard_falls_back_to_slow_keys(desk, monkeypatch):
    monkeypatch.setattr(desk, "clipboard_save", lambda: None)          # another app holds the clipboard
    tools.type_text("hi you")
    assert desk.logged("write") == list("hi you") and desk.logged("press") == [] and desk.logged("clip") == []


def test_a_password_box_is_still_refused_and_nothing_reaches_the_clipboard(desk, monkeypatch):
    monkeypatch.setattr(tools.forms, "_focused_is_password", lambda: True)
    assert tools.type_text("hunter2") == tools.NO_SECRETS
    assert desk.log == []


def test_empty_text_touches_nothing(desk):
    tools.type_text("")
    assert desk.log == []


def test_fill_in_pastes_too(desk, monkeypatch):
    class Ctrl:
        IsPassword = False
    items = [{"name": "Search", "type": "Edit", "x": 1, "y": 1, "offscreen": False, "ctrl": Ctrl()}]
    monkeypatch.setattr(tools.forms, "_scan", lambda window, limit=0: ((1, "Shop", "chrome.exe"), items))
    monkeypatch.setattr(tools.forms, "_nova_click", lambda *a, **k: None)
    tools.fill_in("blue shoes", box="Search")
    assert desk.logged("clip") == ["blue shoes"] and desk.logged("press") == ["ctrl+a", "ctrl+v", "enter"]


def test_waits_for_the_paste_to_land_before_putting_the_clipboard_back(desktop, monkeypatch):
    seen = iter(["", "", "hello"])
    reads = []
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    monkeypatch.setattr(desktop, "focused_text", lambda: (reads.append(1), next(seen))[1])
    tools._wait_for_paste("")
    assert len(reads) == 3                          # stopped as soon as the box showed the pasted text


# ------------------------------------------------------------------ the real clipboard code, with a fake user32

class FakeUser32:
    def __init__(self, seq):
        self.seq, self.calls = seq, []

    def GetClipboardSequenceNumber(self):
        return self.seq

    def OpenClipboard(self, h):
        self.calls.append("open")
        return True

    def CloseClipboard(self):
        self.calls.append("close")
        return True

    def EmptyClipboard(self):
        self.calls.append("empty")
        return True


def test_restore_puts_the_users_things_back_kept_out_of_history(monkeypatch):
    u = FakeUser32(seq=42)
    put = []
    monkeypatch.setattr(win32, "_clip", lambda: (None, u, None))
    monkeypatch.setattr(win32, "_clip_put", lambda items: put.append(items))
    saved = [(13, b"x\0\0\0"), (49999, b"rich")]
    win32.Win32Desktop().clipboard_restore(saved, 42)
    assert put == [saved + win32._KEEP_OUT_OF_HISTORY] and u.calls[-1] == "close"


def test_restore_leaves_something_the_user_copied_meanwhile(monkeypatch):
    u = FakeUser32(seq=43)                          # the clipboard changed after Nova's paste
    monkeypatch.setattr(win32, "_clip", lambda: (None, u, None))
    monkeypatch.setattr(win32, "_clip_put", lambda items: pytest.fail("overwrote the user's new copy"))
    win32.Win32Desktop().clipboard_restore([(13, b"x\0\0\0")], 42)
    assert u.calls == []


def test_restore_of_an_empty_clipboard_empties_it(monkeypatch):
    u = FakeUser32(seq=7)
    monkeypatch.setattr(win32, "_clip", lambda: (None, u, None))
    win32.Win32Desktop().clipboard_restore([], 7)
    assert u.calls == ["open", "empty", "close"]


def test_pasted_text_is_unicode_and_kept_out_of_clipboard_history(monkeypatch):
    u = FakeUser32(seq=5)
    put = []
    monkeypatch.setattr(win32, "_clip", lambda: (None, u, None))
    monkeypatch.setattr(win32, "_clip_put", lambda items: put.append(items))
    assert win32.Win32Desktop().clipboard_set_text("café ✓") == 5
    (items,) = put
    assert items[0] == (13, "café ✓\0".encode("utf-16-le"))
    assert {"CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"} <= {f for f, _ in items[1:]}
