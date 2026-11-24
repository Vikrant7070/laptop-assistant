"""Routine suggestions (manager plan Phase 8, the safe part): the right moment, once, limits, never, prepare-ahead."""
import datetime as dt
import types

import pytest

from nova import platform
from nova.actions import suggest, tools
from nova.core import app
from nova.core.app import asking, config, suggesting
from nova.platform import busy

TUE_9 = dt.datetime(2026, 11, 24, 9, 0)          # a Tuesday
MORNING = {"id": "morning", "name": "morning setup", "steps": ["Gmail", "Calendar", "VS Code"], "time": "09:00",
           "days": "weekdays"}
CALM = {"present": True, "busy": False, "asleep": False, "question": False, "fullscreen": "", "title": "Explorer",
        "proc": "explorer.exe"}


@pytest.fixture
def routines(monkeypatch):
    """routines(list): Phase 7's routines(now=None) gives these. Also a calm moment, and spawn recorded."""
    found = []
    monkeypatch.setattr(suggest, "source", lambda: (lambda now=None: list(found)))
    ctx = dict(CALM)
    monkeypatch.setattr(suggesting, "context", lambda: dict(ctx))
    spawned = []
    monkeypatch.setattr(suggesting.runtime, "spawn", lambda name, fn, args=(), **k: spawned.append((fn, args)))
    monkeypatch.setattr(tools, "run_tool", lambda n, a: "ok")               # prepare-ahead never reaches a real tool
    suggesting.register()
    suggesting.prepared.clear()

    def install(*rs):
        found[:] = rs
        return types.SimpleNamespace(ctx=ctx, spawned=spawned)
    yield install
    app.state.pop("question", None)


def _close_card():
    app.state.pop("question", None)


def _tap(client, choice):
    card = asking.current()
    return client.post("/api/answer", json={"id": card["id"], "answers": {"go": choice}})


# --- the moment ---------------------------------------------------------------------------------------------------

def test_a_routine_at_9_gives_one_card_at_9_and_none_at_15(routines):
    routines(MORNING)
    assert suggesting.tick(dt.datetime(2026, 11, 24, 15, 0)) is None and asking.current() is None
    out = suggesting.tick(TUE_9)
    assert out["question"]["questions"][0]["text"] == "Open your morning setup? (Gmail → Calendar → VS Code)"
    assert [c["label"] for c in out["question"]["questions"][0]["choices"]] == ["Yes", "Not now", "Don't suggest this"]
    assert any(e.get("kind") == "suggestion" and e.get("question") for e in app.events)
    _close_card()
    assert suggesting.tick(TUE_9 + dt.timedelta(minutes=5)) is None        # offered once
    assert suggesting.tick(dt.datetime(2026, 11, 25, 9, 10)) is not None   # next day, again


def test_the_window_and_the_days(routines):
    routines(MORNING)
    assert suggesting.tick(TUE_9 - dt.timedelta(minutes=suggest.WINDOW_MIN + 1)) is None
    assert suggesting.tick(dt.datetime(2026, 11, 29, 9, 0)) is None         # a Sunday: weekdays only
    assert suggesting.tick(TUE_9 + dt.timedelta(minutes=suggest.WINDOW_MIN)) is not None


@pytest.mark.parametrize("change,why", [
    ({"busy": True}, "a task is running"),
    ({"fullscreen": "a full-screen game"}, "full screen"),
    ({"fullscreen": "full screen"}, "full screen"),
    ({"proc": "ms-teams.exe", "title": "Weekly sync | Meeting | Microsoft Teams"}, "meeting"),
    ({"proc": "chrome.exe", "title": "Meet - abc-defg-hij - Google Chrome"}, "meeting"),
    ({"present": False}, "isn't at the laptop"),
    ({"question": True}, "question"),
    ({"asleep": True}, "asleep"),
])
def test_no_card_while_busy_full_screen_in_a_meeting_or_away(routines, change, why):
    r = routines(MORNING)
    r.ctx.update(change)
    assert suggesting.tick(TUE_9) is None and asking.current() is None
    assert why in suggesting.last["why"]
    r.ctx.update(CALM)
    assert suggesting.tick(TUE_9 + dt.timedelta(minutes=1)) is not None      # it wasn't used up


def test_teams_open_is_not_a_meeting():
    assert suggest.blocked({**CALM, "proc": "ms-teams.exe", "title": "Chat | Microsoft Teams"}) == ""


def test_quiet_hours_and_the_off_switch(routines):
    late = {**MORNING, "id": "late", "time": "23:00", "days": None}
    routines(late)
    assert suggesting.tick(dt.datetime(2026, 11, 24, 23, 0)) is None and suggesting.last["why"] == "quiet hours"
    s = config.settings()
    s["routine_quiet"] = ""
    config.save_settings(s)
    assert suggesting.tick(dt.datetime(2026, 11, 24, 23, 0)) is not None
    _close_card()
    s["routine_suggest"] = False
    config.save_settings(s)
    assert suggesting.tick(dt.datetime(2026, 11, 25, 23, 0)) is None


def test_the_daily_limit_holds(routines):
    routines(*({**MORNING, "id": f"r{i}", "name": f"setup {i}", "time": f"09:{i * 5:02d}"} for i in range(3)))
    assert suggesting.tick(TUE_9) is not None
    _close_card()
    assert suggesting.tick(TUE_9 + dt.timedelta(minutes=5)) is not None
    _close_card()
    assert suggesting.tick(TUE_9 + dt.timedelta(minutes=10)) is None
    assert "daily limit (2)" in suggesting.last["why"]
    assert len(suggest.load(TUE_9)["shown"]) == 2


# --- the answers --------------------------------------------------------------------------------------------------

def test_dont_suggest_this_stops_it_for_good(routines, client):
    routines(MORNING)
    suggesting.tick(TUE_9)
    r = _tap(client, "never")
    assert r.status_code == 200 and "won't suggest that one again" in r.get_json()["reply"]
    for day in range(25, 30):
        assert suggesting.tick(dt.datetime(2026, 11, day, 9, 0)) is None
    listed = client.get("/api/suggestions").get_json()
    assert listed["never"] == [{"id": "morning", "name": "morning setup"}]
    client.post("/api/suggestions", json={"allow": "morning"})                 # ⚙ Senses: allow it again
    assert suggesting.tick(dt.datetime(2026, 12, 1, 9, 0)) is not None


def test_not_now_snoozes_everything_for_an_hour(routines, client):
    routines(MORNING, {**MORNING, "id": "other", "name": "other setup", "time": "09:10"})
    suggesting.tick(TUE_9)
    assert "Not now" in _tap(client, "later").get_json()["activity"]
    assert suggesting.tick(TUE_9 + dt.timedelta(minutes=10)) is None and suggesting.last["why"] == "snoozed"


def test_yes_runs_it_as_the_users_request_through_the_normal_path(routines, client, monkeypatch):
    r = routines(MORNING)
    suggesting.tick(TUE_9)
    ran = []
    monkeypatch.setattr(tools, "run_tool", lambda n, a: (ran.append(n), "ok")[1])
    reply = _tap(client, "yes").get_json()
    assert ran == []                                                          # nothing ran on the tap itself
    assert reply["reply"].startswith("Okay: Open Gmail, then open Calendar, then open VS Code")
    asked = []
    monkeypatch.setattr(app.body, "answer", lambda text, **k: (asked.append(text), ({"reply": "Opened them."}, 200))[1])
    (fn, args), = r.spawned
    fn(*args)                                                                 # the spawned run, after the tap
    assert asked == ["Open Gmail, then open Calendar, then open VS Code"]
    assert {"me": "Open Gmail, then open Calendar, then open VS Code"} in app.events


def test_an_unanswered_card_still_counts_as_offered(routines):
    routines(MORNING)
    assert suggesting.tick(TUE_9) is not None
    asking._drop(asking.current(), "expired")
    assert [a["choice"] for a in suggest.load(TUE_9)["answers"]] == ["dropped"]
    assert suggesting.tick(TUE_9 + dt.timedelta(minutes=2)) is None


# --- prepare ahead ------------------------------------------------------------------------------------------------

def test_prepare_ahead_never_runs_a_level_1_or_2_step(routines, monkeypatch):
    ran = []
    monkeypatch.setattr(tools, "run_tool", lambda n, a: (ran.append(n), "3 new emails")[1])
    risky = {"id": "mixed", "name": "morning setup", "time": "09:00", "steps": [
        "Gmail",                                                              # open_app (1) + its mail read (0)
        {"label": "clean up", "tool": "delete_to_recycle_bin", "args": {"path": "C:/x"}},           # 2
        {"label": "date", "tool": "run_powershell", "args": {"command": "Get-Date"}},              # 0, but shell
        {"label": "post it", "tool": "slack_send_message", "args": {}},                             # connector: 2
        {"label": "notes", "tool": "write_text_file", "args": {"path": "a.txt", "text": "x"}},      # 1
        {"label": "status", "tool": "get_system_status", "args": {}},                               # 0, allowed
    ]}
    r = routines(risky)
    r.ctx["present"] = False                                                  # prepare-ahead doesn't need the user
    suggesting.tick(TUE_9 - dt.timedelta(minutes=suggest.WINDOW_MIN + 5))
    assert ran == ["check_email", "get_system_status"]
    assert asking.current() is None                                           # no card: too early, and away
    suggesting.tick(TUE_9 - dt.timedelta(minutes=suggest.WINDOW_MIN + 4))
    assert ran == ["check_email", "get_system_status"]                        # once a day


def test_prepared_reads_are_shown_on_yes_not_given_to_the_brain(routines, client, monkeypatch):
    monkeypatch.setattr(tools, "run_tool", lambda n, a: "From: Ana - Lunch?")
    r = routines(MORNING)
    suggesting.tick(TUE_9)
    _tap(client, "yes")
    monkeypatch.setattr(app.body, "answer", lambda text, **k: ({"reply": "ok"}, 200))
    (fn, args), = r.spawned
    fn(*args)
    shown = [e["reply"] for e in app.events if e.get("kind") == "suggestion" and "Ready ahead" in (e.get("reply") or "")]
    assert shown and "From: Ana - Lunch?" in shown[0]
    assert args[0] == "Open Gmail, then open Calendar, then open VS Code"    # the brain gets the request only


def test_prepare_calls_only_level_0_on_the_allow_list():
    for tool in suggest.PREPARE_OK:
        assert suggest.risk.level(tool, {}) == 0
    r = suggest.normalise({"id": "x", "time": "8:30", "steps": [{"tool": "look_at_screen"}, {"tool": "web_research"}]})
    assert suggest.prepare_calls(r) == []                                     # level 0, but not on the list


# --- Phase 7's routines through the adapter -----------------------------------------------------------------------

def test_the_adapter_returns_nothing_while_phase_7_is_missing(monkeypatch):
    monkeypatch.setattr(suggest, "SOURCES", ("nova.actions.no_such_phase7_module",))
    assert suggest.source() is None and suggest.found_routines() == []


def test_the_adapter_survives_a_failing_phase_7(monkeypatch):
    def boom(now=None):
        raise RuntimeError("bad log")
    monkeypatch.setattr(suggest, "source", lambda: boom)
    assert suggest.found_routines() == []


@pytest.mark.parametrize("raw,minute,days", [
    ({"steps": ["A"], "time": "9:05"}, 545, None),
    ({"apps": ["A"], "minute_of_day": 600, "days": [0, 2]}, 600, [0, 2]),
    ({"sequence": [{"app": "A"}], "hour": 7.5, "days": ["Sat", "sunday"]}, 450, [5, 6]),
])
def test_the_adapter_reads_the_shapes_phase_7_may_give(raw, minute, days):
    r = suggest.normalise(raw)
    assert r["minute"] == minute and r["days"] == days and r["steps"][0]["label"] == "A"


@pytest.mark.parametrize("raw", [{"steps": [], "time": "9:00"}, {"steps": ["A"]}, {"steps": ["A"], "time": "25:00"},
                                 "not a dict"])
def test_the_adapter_skips_routines_it_cant_use(raw):
    assert suggest.normalise(raw) is None


# --- full screen, from the desktop --------------------------------------------------------------------------------

def test_a_window_covering_the_monitor_is_full_screen(monkeypatch):
    monkeypatch.setattr(busy, "notification_state", lambda: "")
    d = platform.current()
    d.set_windows([(5, "Movie", "vlc.exe")], front=5)
    d.monitor = (0, 0, 1920, 1080)
    d.rects = {5: (0, 0, 1920, 1080)}
    assert suggesting._fullscreen(5) == "full screen"
    d.rects = {5: (100, 100, 900, 700)}
    assert suggesting._fullscreen(5) == ""
    monkeypatch.setattr(busy, "notification_state", lambda: "presentation mode")
    assert suggesting._fullscreen(5) == "presentation mode"


def test_settings_endpoint_checks_quiet_hours(client):
    assert client.post("/api/suggestions", json={"quiet": "late"}).status_code == 400
    j = client.post("/api/suggestions", json={"quiet": "21:30-06:00", "max": 3, "on": False}).get_json()
    assert (j["quiet"], j["max"], j["on"]) == ("21:30-06:00", 3, False)
