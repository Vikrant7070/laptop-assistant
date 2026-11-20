"""Reading notifications aloud (only when switched on) and suggestions about what's on the screen."""
import json
import time

import pytest

from nova.core import app
from nova.actions import notifications
from nova.actions import tools


def set_settings(**kw):
    s = app.settings()
    s.update(kw)
    app.SETTINGS_FILE.write_text(json.dumps(s), encoding="utf-8")


N1 = {"id": 1, "app": "WhatsApp", "title": "Mom", "body": "Call me when you're free", "time": time.time()}
OTP = {"id": 2, "app": "Messages", "title": "Bank", "body": "Your OTP is 482913. Do not share it.", "time": time.time()}


# ---------------------------------------------------------------- reading them

def test_parse_and_describe():
    d = notifications._parse('{"id": 5, "app": "Outlook", "texts": "Only a title", "time": 3}')
    assert d == {"id": 5, "app": "Outlook", "title": "Only a title", "body": "", "time": 3}
    d = notifications._parse('{"id": 6, "app": "", "texts": ["Hi", "  line  two ", "three"], "time": 3}')
    assert d["app"] == "An app" and d["body"] == "line two three"
    assert notifications.describe(N1) == "WhatsApp: Mom - Call me when you're free"
    long = dict(N1, body="x" * 500)
    assert len(notifications.describe(long, 100)) == 100
    assert notifications._parse("garbage") is None
    assert notifications._parse('{"error": "access Denied"}') == {"error": "access Denied"}


def test_tool_respects_the_switch(monkeypatch):
    monkeypatch.setattr(notifications, "latest", lambda count=5: [dict(N1, time=time.time())])
    assert "switched off" in tools.read_notifications()
    set_settings(notifications=True)
    out = tools.read_notifications()
    assert "WhatsApp: Mom - Call me" in out and "just now" in out


def test_tool_when_there_are_none(monkeypatch):
    set_settings(notifications=True)
    monkeypatch.setattr(notifications, "latest", lambda count=5: [])
    assert "no notifications" in tools.read_notifications()


def test_new_notification_is_read_out():
    set_settings(notifications=True)
    app.on_notifications([N1])
    assert app.mouth.said == ["WhatsApp: Mom - Call me when you're free"]
    assert app.events[-1] == {"reply": "🔔 WhatsApp: Mom - Call me when you're free", "kind": "notification"}


def test_nothing_read_when_switched_off():
    app.on_notifications([N1])
    assert app.mouth.said == [] and app.events == []


def test_codes_are_never_read_out():
    set_settings(notifications=True)
    app.on_notifications([OTP])
    assert "482913" not in app.mouth.said[0] and "(hidden)" in app.mouth.said[0]
    assert "482913" not in json.dumps(app.events)


def test_a_burst_is_one_announcement():
    set_settings(notifications=True)
    many = [dict(N1, id=i, title=f"Friend {i}") for i in range(5)]
    app.on_notifications(many)
    assert len(app.mouth.said) == 1 and app.mouth.said[0].startswith("5 new notifications")
    assert "and 2 more" in app.mouth.said[0]
    assert len([e for e in app.events if e.get("kind") == "notification"]) == 5


def test_watcher_batches_lines(monkeypatch):
    got = []
    w = notifications.Watcher(lambda items: got.append(items))
    for n in (N1, OTP):
        w._add(n)
    end = time.time() + 2
    while not got and time.time() < end:
        time.sleep(0.02)
    assert got == [[N1, OTP]]


def test_setup_switch_starts_and_stops_the_watcher(client, monkeypatch):
    calls = []
    monkeypatch.setattr(app.notif_watcher, "start", lambda: calls.append("start"))
    monkeypatch.setattr(app.notif_watcher, "stop", lambda: calls.append("stop"))
    client.post("/api/setup", json={"notifications": True})
    assert app.settings()["notifications"] is True
    client.post("/api/setup", json={"notifications": False})
    assert calls == ["start", "stop"]
    s = client.get("/api/status").get_json()
    assert s["notifications"] is False and s["suggest"] is False


# ---------------------------------------------------------------- suggestions

@pytest.fixture
def screen(monkeypatch):
    sent = []
    monkeypatch.setattr(tools, "_screen_allowed", lambda: True)
    monkeypatch.setattr(tools, "_grab_screen_jpeg", lambda *a, **k: "IMG")
    monkeypatch.setattr(app, "_front_title", lambda: "report.docx - Word")
    monkeypatch.setattr(app.avatar_client, "send", lambda do, timeout=20, **a: sent.append((do, a)))
    app.suggest_state.update(last=0.0, title="", running=False)
    return sent


def test_suggestion_is_shown(screen, monkeypatch):
    seen = []
    monkeypatch.setattr(app.llm, "see", lambda img, q, preferred=None, kind="": (seen.append((img, kind)), "Suggestion: Press Ctrl+S - you haven't saved in a while.")[1])
    tip = app.suggest_now()
    assert tip == "Press Ctrl+S - you haven't saved in a while."
    assert seen == [("IMG", "check")]
    assert app.events[-1] == {"reply": "💡 " + tip, "kind": "suggestion"}
    assert screen[-1][0] == "say" and tip in screen[-1][1]["text"]


def test_nothing_useful_means_silence(screen, monkeypatch):
    monkeypatch.setattr(app.llm, "see", lambda *a, **k: "NONE")
    assert app.suggest_now() is None and app.events == []
    app.suggest_state.update(last=0.0, title="")
    monkeypatch.setattr(app.llm, "see", lambda *a, **k: "WORKING ON: watching a video\nSUGGESTION: NONE")
    assert app.suggest_now() is None and app.events == []


def test_two_line_answer_gives_the_tip(screen, monkeypatch):
    monkeypatch.setattr(app.llm, "see", lambda *a, **k: "WORKING ON: a Word report\nSUGGESTION: Add page numbers before you print.")
    assert app.suggest_now() == "Add page numbers before you print."


def test_same_window_not_repeated_unless_asked(screen, monkeypatch):
    monkeypatch.setattr(app.llm, "see", lambda *a, **k: "Add a title to this report.")
    assert app.suggest_now()
    assert app.suggest_now() is None                  # same window, just now
    assert app.suggest_now(force=True)               # the 💡 button always looks


def test_no_suggestions_without_screen_permission(monkeypatch):
    monkeypatch.setattr(app.llm, "see", lambda *a, **k: pytest.fail("looked without permission"))
    assert app.suggest_now(force=True) is None


def test_never_about_novas_own_window(screen, monkeypatch):
    monkeypatch.undo()                     # the real _front_title, with a made-up window list
    monkeypatch.setattr(tools, "_windows", lambda: [(1, "Nova 3D", "pythonw.exe"), (2, "Nova", "pythonw.exe"),
                                                    (3, "budget.xlsx - Excel", "EXCEL.EXE")])
    assert app._front_title() == "budget.xlsx - Excel"
    monkeypatch.setattr(tools, "_windows", lambda: [(1, "Nova 3D", "pythonw.exe")])
    monkeypatch.setattr(tools, "_screen_allowed", lambda: True)
    monkeypatch.setattr(app.llm, "see", lambda *a, **k: pytest.fail("looked at Nova herself"))
    assert app.suggest_now() is None


def test_suggest_button(client, screen, monkeypatch):
    monkeypatch.setattr(app.llm, "see", lambda *a, **k: "NONE")
    j = client.post("/api/suggest", json={}).get_json()
    assert "nothing to suggest" in j["reply"]


def test_asking_for_suggestions_looks_at_the_screen():
    for t in ("any suggestions for what I'm working on?", "give me some tips on this", "what should I do next"):
        assert app.SCREEN_ASK.search(t), t
    assert not app.SCREEN_ASK.search("suggest a good movie")


# ---------------------------------------------------------------- muting one app ("don't read Claude's notifications")

CLAUDE = {"id": 9, "app": "Claude", "title": "Claude", "body": "Your task is done", "time": time.time()}


def test_same_app_is_forgiving_but_not_sloppy():
    assert notifications.same_app("Claude", "cloude")          # speech recognition garble
    assert notifications.same_app("claude", "Claude (desktop)")
    assert not notifications.same_app("Mail", "Gmail")
    assert not notifications.same_app("WhatsApp", "Claude")
    assert notifications.resolve_app("cloude app", {"Claude", "WhatsApp"}) == "Claude"
    assert notifications.resolve_app("telegram", set()) == "Telegram"


def test_mute_and_resume_an_app():
    set_settings(notifications=True)
    notifications.recent_apps.add("Claude")
    assert "won't read notifications from Claude" in tools.mute_notifications("cloude")
    assert app.settings()["notif_muted"] == ["Claude"]
    assert "already muted" in tools.mute_notifications("Claude")
    assert "Claude" in tools.mute_notifications()               # list them

    app.on_notifications([CLAUDE])                              # muted: not read, not shown
    assert app.mouth.said == [] and app.events == []
    app.on_notifications([CLAUDE, N1])                          # the others still are
    assert app.mouth.said == ["WhatsApp: Mom - Call me when you're free"]

    assert "read notifications from Claude again" in tools.mute_notifications("claude", mute=False)
    assert app.settings()["notif_muted"] == []
    app.mouth.said.clear()
    app.on_notifications([CLAUDE])
    assert app.mouth.said == ["Claude: Claude - Your task is done"]


def test_read_notifications_skips_muted_apps(monkeypatch):
    set_settings(notifications=True, notif_muted=["Claude"])
    monkeypatch.setattr(notifications, "latest", lambda count=5: [CLAUDE, N1])
    out = tools.read_notifications()
    assert "WhatsApp" in out and "Your task is done" not in out and "Muted apps" in out


def test_unmute_all():
    set_settings(notif_muted=["Claude", "Slack"])
    assert "every app again" in tools.mute_notifications("all", mute=False)
    assert app.settings()["notif_muted"] == []


def test_mute_request_finds_the_tool():
    from nova.brain import toolpick
    assert "mute_notifications" in toolpick.pick("don't read notifications from claude app")
    assert "mute_notifications" in toolpick.pick("resume reading claude notifications")
