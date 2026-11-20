"""type_text puts text in with one paste (not letter-by-letter keys, which Windows 11 Notepad garbled into
"hello eeeee") and gives the user's clipboard back. No real keys are pressed and the real clipboard is never
touched: the clipboard calls are fakes."""
import pytest

from nova.actions import tools

REAL_CLIP_SET_TEXT = tools._clip_set_text      # (conftest swaps these for fakes in every test)
REAL_CLIP_RESTORE = tools._clip_restore


@pytest.fixture
def desk(monkeypatch):
    """A pretend desk: what was put on the clipboard, which keys were pressed, what was put back."""
    rec = {"clip": [], "sent": [], "written": [], "restored": []}
    monkeypatch.setattr(tools, "_focused_is_password", lambda: False)
    monkeypatch.setattr(tools, "_point_at_focus", lambda label: False)
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    monkeypatch.setattr(tools, "_clip_save", lambda: [(13, "user's own text\0".encode("utf-16-le"))])
    monkeypatch.setattr(tools, "_clip_set_text", lambda text: (rec["clip"].append(text), 42)[1])
    monkeypatch.setattr(tools, "_clip_restore", lambda saved, seq: rec["restored"].append((saved, seq)))
    monkeypatch.setattr(tools.keyboard, "send", lambda keys: rec["sent"].append(keys))
    monkeypatch.setattr(tools.keyboard, "write", lambda text, delay=0: rec["written"].append(text))
    return rec


def test_type_text_pastes_once_and_gives_the_clipboard_back(desk):
    assert tools.type_text("hello judge") == "Typed 11 characters."
    assert desk["clip"] == ["hello judge"]
    assert desk["sent"] == ["ctrl+v"] and desk["written"] == []          # one paste, no letter-by-letter keys
    assert desk["restored"] == [([(13, "user's own text\0".encode("utf-16-le"))], 42)]


def test_a_longer_sentence_with_new_lines_goes_in_whole(desk):
    text = "The quick brown fox jumps over the lazy dog.\nSecond line: 100% done, café ✓"
    tools.type_text(text)
    assert desk["clip"] == [text] and desk["sent"] == ["ctrl+v"]


def test_the_clipboard_comes_back_even_if_pasting_fails(desk, monkeypatch):
    def boom(keys):
        raise RuntimeError("keyboard hook gone")
    monkeypatch.setattr(tools.keyboard, "send", boom)
    with pytest.raises(RuntimeError):
        tools.type_text("hello")
    assert len(desk["restored"]) == 1


def test_busy_clipboard_falls_back_to_slow_keys(desk, monkeypatch):
    monkeypatch.setattr(tools, "_clip_save", lambda: None)          # another app holds the clipboard
    tools.type_text("hi you")
    assert desk["written"] == list("hi you") and "ctrl+v" not in desk["sent"] and desk["clip"] == []


def test_a_password_box_is_still_refused_and_nothing_reaches_the_clipboard(desk, monkeypatch):
    monkeypatch.setattr(tools, "_focused_is_password", lambda: True)
    assert tools.type_text("hunter2") == tools.NO_SECRETS
    assert desk["clip"] == [] and desk["sent"] == [] and desk["written"] == []


def test_empty_text_touches_nothing(desk):
    tools.type_text("")
    assert desk["clip"] == [] and desk["sent"] == []


def test_fill_in_pastes_too(desk, monkeypatch):
    class Ctrl:
        IsPassword = False
    items = [{"name": "Search", "type": "Edit", "x": 1, "y": 1, "offscreen": False, "ctrl": Ctrl()}]
    monkeypatch.setattr(tools, "_scan", lambda window, limit=0: ((1, "Shop", "chrome.exe"), items))
    monkeypatch.setattr(tools, "_focus", lambda h: None)
    monkeypatch.setattr(tools, "_nova_click", lambda *a, **k: None)
    tools.fill_in("blue shoes", box="Search")
    assert desk["clip"] == ["blue shoes"] and desk["sent"] == ["ctrl+a", "ctrl+v", "enter"]


def test_waits_for_the_paste_to_land_before_putting_the_clipboard_back(monkeypatch):
    seen = iter(["", "", "hello"])
    reads = []
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    monkeypatch.setattr(tools, "_focused_text", lambda: (reads.append(1), next(seen))[1])
    tools._wait_for_paste("")
    assert len(reads) == 3                          # stopped as soon as the box showed the pasted text


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
    monkeypatch.setattr(tools, "_clip", lambda: (None, u, None))
    monkeypatch.setattr(tools, "_clip_put", lambda items: put.append(items))
    saved = [(13, b"x\0\0\0"), (49999, b"rich")]
    REAL_CLIP_RESTORE(saved, 42)
    assert put == [saved + tools._KEEP_OUT_OF_HISTORY] and u.calls[-1] == "close"


def test_restore_leaves_something_the_user_copied_meanwhile(monkeypatch):
    u = FakeUser32(seq=43)                          # the clipboard changed after Nova's paste
    monkeypatch.setattr(tools, "_clip", lambda: (None, u, None))
    monkeypatch.setattr(tools, "_clip_put", lambda items: pytest.fail("overwrote the user's new copy"))
    REAL_CLIP_RESTORE([(13, b"x\0\0\0")], 42)
    assert u.calls == []


def test_restore_of_an_empty_clipboard_empties_it(monkeypatch):
    u = FakeUser32(seq=7)
    monkeypatch.setattr(tools, "_clip", lambda: (None, u, None))
    REAL_CLIP_RESTORE([], 7)
    assert u.calls == ["open", "empty", "close"]


def test_pasted_text_is_unicode_and_kept_out_of_clipboard_history(monkeypatch):
    u = FakeUser32(seq=5)
    put = []
    monkeypatch.setattr(tools, "_clip", lambda: (None, u, None))
    monkeypatch.setattr(tools, "_clip_put", lambda items: put.append(items))
    assert REAL_CLIP_SET_TEXT("café ✓") == 5
    (items,) = put
    assert items[0] == (13, "café ✓\0".encode("utf-16-le"))
    assert {"CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"} <= {f for f, _ in items[1:]}
