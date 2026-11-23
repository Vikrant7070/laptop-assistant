"""The hands-off glow (nova/overlay.py): the picture, the controller, when Nova shows it, and the exam's notices."""
import io
import json

import pytest

from nova import exam, overlay, task_state as ts
from nova.core import app
from nova.core.app import resume


# --- the picture ----------------------------------------------------------------------------------------------

def test_the_glow_is_at_the_edges_and_the_middle_is_see_through():
    img = overlay.render(400, 300, "Nova is using your laptop")
    assert img.size == (400, 300) and img.mode == "RGBA"
    assert img.getpixel((0, 150))[3] > 150 and img.getpixel((399, 150))[3] > 150        # left and right edges glow
    assert img.getpixel((200, 299))[3] > 150                                            # the bottom too
    assert img.getpixel((200, 150))[3] == 0                                             # the middle is clear
    assert img.getpixel((200, overlay.GLOW + 20))[3] > 200                              # the pill at the top
    assert overlay.render(400, 300, "").getpixel((200, overlay.GLOW + 20))[3] == 0      # no text, no pill


def test_every_screen_glows_and_the_pill_sits_on_the_primary():
    # a 400x300 primary on the right of a 200x200 second screen that starts 50 px lower
    img = overlay.render(600, 300, "Nova is using your laptop", [(200, 0, 400, 300), (0, 50, 200, 200)])
    assert img.getpixel((200, 150))[3] > 150 and img.getpixel((599, 150))[3] > 150    # primary's left and right edges
    assert img.getpixel((0, 150))[3] > 150 and img.getpixel((199, 150))[3] > 150      # the second screen's edges
    assert img.getpixel((100, 150))[3] == 0                                           # its middle is clear
    assert img.getpixel((100, 10))[3] == 0                                            # off every screen: nothing
    assert img.getpixel((400, overlay.GLOW + 20))[3] > 200                            # the pill: middle of the primary
    assert img.getpixel((100, 50 + overlay.GLOW + 20))[3] == 0                        # not on the second screen


def test_one_window_spans_every_screen():
    from nova.platform import overlay_window
    rect, inside = overlay_window.layout([(0, 0, 1920, 1080), (-1280, 100, 1280, 1024)])
    assert rect == (-1280, 0, 3200, 1124)
    assert inside == [(1280, 0, 1920, 1080), (0, 100, 1280, 1024)]                   # primary stays first


def test_the_glow_breathes_but_never_fades_out():
    seen = [overlay.pulse(t / 10) for t in range(int(overlay.PULSE_SECS * 20))]
    assert overlay.pulse(0) == 255 and max(seen) == 255
    assert min(seen) >= overlay.PULSE_LOW > 100                                        # the pill stays readable
    assert overlay.pulse(overlay.PULSE_SECS / 2) == overlay.PULSE_LOW


def test_premultiplied_bgra_for_update_layered_window():
    from PIL import Image
    img = Image.new("RGBA", (1, 1), (200, 100, 50, 128))
    assert overlay.premultiplied_bgra(img) == bytes([50 * 128 // 255, 100 * 128 // 255, 200 * 128 // 255, 128])


# --- the controller -------------------------------------------------------------------------------------------

class FakeProc:
    def __init__(self):
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None
        self.waited = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        self.waited = True

    def lines(self):
        return [json.loads(x) for x in self.stdin.getvalue().decode().splitlines()]


@pytest.fixture
def ctl(monkeypatch):
    started = []
    monkeypatch.setattr(overlay, "can_show", lambda: True)
    monkeypatch.setattr(overlay.subprocess, "Popen", lambda *a, **k: started.append(FakeProc()) or started[-1])
    c = overlay.Overlay()
    c.started = started
    return c


def test_the_controller_starts_once_and_sends_show_and_hide(ctl):
    ctl.hide()                                   # nothing shown: nothing started
    assert ctl.started == []
    ctl.show("A")
    ctl.show("A")                                # same text: not sent again
    ctl.show("B")
    ctl.hide()
    ctl.hide()
    assert len(ctl.started) == 1
    assert ctl.started[0].lines() == [{"do": "show", "text": "A"}, {"do": "show", "text": "B"}, {"do": "hide"}]
    ctl.close()
    assert ctl.started[0].waited and ctl.proc is None


def test_never_in_the_tests_or_off_windows(monkeypatch):
    assert overlay.can_show() is False                                   # pytest is running
    started = []
    monkeypatch.setattr(overlay.subprocess, "Popen", lambda *a, **k: started.append(1))
    overlay.Overlay().show("x")
    assert started == []


# --- when Nova shows it ---------------------------------------------------------------------------------------

@pytest.fixture
def glow(monkeypatch):
    calls = []
    monkeypatch.setattr(resume.overlay, "show", lambda text=overlay.TASK_TEXT: calls.append(("show", text)))
    monkeypatch.setattr(resume.overlay, "hide", lambda: calls.append(("hide",)))
    return calls


def _card(*tools):
    card = ts.new("tidy my desktop")
    for t in tools:
        ts.record(card, {"name": t, "args": {}}, "ok", True)
    return card


def test_the_glow_shows_while_a_task_uses_the_keyboard_or_mouse(glow):
    card = _card("get_datetime")
    glow.clear()                                                         # (making the card already called the hook)
    resume._hands_off(card)
    assert glow == [("hide",)]                                           # reading the time: no glow
    card = _card("get_datetime", "type_text")
    glow.clear()
    resume._hands_off(card)
    assert glow[-1] == ("show", overlay.TASK_TEXT)
    for status in ("waiting", "done", "stopped", "failed"):              # a question, the end, Stop: it goes
        ts.mark(card, status)
        resume._hands_off(card)
        assert glow[-1] == ("hide",)


def test_the_setting_turns_it_off(glow, monkeypatch):
    card = _card("open_app")
    glow.clear()
    monkeypatch.setattr(app.config, "settings", lambda: {"hands_off_glow": False})
    resume._hands_off(card)
    assert glow == [("hide",)]


def test_a_real_task_turns_it_on_then_off(brain, glow):
    s = app.settings()
    s["autonomy"] = "trusted"
    from nova import store
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")
    brain([("", [("click_element", {"name": "a"})]), ("Done.", [])])
    app.answer("tidy up my desktop icons")
    shows = [c for c in glow if c[0] == "show"]
    assert shows and glow[-1] == ("hide",)


def test_the_switch_is_in_senses(client):
    assert client.get("/api/status").get_json()["hands_off_glow"] is True
    client.post("/api/setup", json={"hands_off_glow": False})
    assert app.settings()["hands_off_glow"] is False
    page = (app.ROOT / "static" / "index.html").read_text(encoding="utf-8")
    js = (app.ROOT / "static" / "nova.js").read_text(encoding="utf-8")
    assert 'id="handsOffGlow"' in page and "hands_off_glow: $(\"#handsOffGlow\").checked" in js


# --- the exam -------------------------------------------------------------------------------------------------

def test_the_exam_wont_start_while_notepad_is_open(monkeypatch, capsys):
    monkeypatch.setattr(exam, "notepad_open", lambda: True)
    assert exam.main(["--model", "x", "--yes"]) == 2
    assert "Save and close its tabs first" in capsys.readouterr().out


def test_the_exam_counts_down_with_the_glow(monkeypatch):
    shown, slept = [], []
    monkeypatch.setattr(exam.overlay, "show", lambda text: shown.append(text))
    exam.hands_off_countdown(25, sleep=slept.append)
    assert len(shown) == exam.COUNTDOWN_SECS and slept == [1] * exam.COUNTDOWN_SECS
    assert shown[0].startswith("Nova's task exam starts in 10 s") and "don't touch the keyboard or mouse" in shown[0]
