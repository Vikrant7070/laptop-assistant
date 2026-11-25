"""📊 Activity log level 1 (manager plan Phase 7): off records nothing, blocked apps / sites / private windows are never
recorded, secret-looking titles are cleaned, paused records nothing, only while the user is here, old spans go, and
routines are found by plain code on 3 fake days. All on a FakeDesktop - never the real one."""
import json
from datetime import datetime, timedelta

import pytest

from nova.actions import activity, learner


@pytest.fixture
def scene(desktop, monkeypatch):
    """A desktop with VS Code in front and a sampler that thinks the user is here."""
    desktop.set_windows([(1, "main.py - nova - Visual Studio Code", "Code.exe"),
                         (2, "Inbox - Gmail - Google Chrome", "chrome.exe")], front=1)
    monkeypatch.setattr(activity, "read_url", lambda hwnd: {2: "https://mail.google.com/mail/u/0/#inbox"}.get(hwnd, ""))
    monkeypatch.setattr(activity, "read_focus", lambda: None)
    return desktop


def _run(sampler, desktop, steps, t0=1_000_000.0):
    """steps: [(front hwnd, seconds)] - one tick every 3 s."""
    t = t0
    for hwnd, secs in steps:
        desktop.front = hwnd
        for _ in range(int(secs // 3)):
            sampler.tick(t)
            t += 3
    sampler.stop(t)
    return t


def test_off_by_default_records_nothing(scene):
    s = activity.Sampler(user_here=lambda now: True)
    _run(s, scene, [(1, 30), (2, 30)])
    assert activity.prefs()["on"] is False and s.state == "off"
    assert activity.spans() == []


def test_on_records_app_title_site_and_times(scene):
    activity.save_prefs(on=True)
    s = activity.Sampler(user_here=lambda now: True)
    _run(s, scene, [(1, 30), (2, 30)])
    rows = activity.spans()
    assert [(r["app"], r["host"]) for r in rows] == [("Code.exe", ""), ("chrome.exe", "mail.google.com")]
    assert rows[0]["title"] == "main.py - nova - Visual Studio Code"          # "Code" is not a secret word
    assert rows[0]["end"] - rows[0]["start"] >= 27 and rows[1]["start"] >= rows[0]["end"]


def test_a_blocked_app_is_never_recorded(scene):
    scene.add(3, "Vault - 1Password", "1Password.exe", front=True)
    activity.save_prefs(on=True)
    s = activity.Sampler(user_here=lambda now: True)
    _run(s, scene, [(3, 30), (1, 30), (3, 30)])
    assert all(r["app"] != "1Password.exe" and "1Password" not in r["title"] for r in activity.spans())
    assert [r["app"] for r in activity.spans()] == ["Code.exe"]


def test_blocked_sites_and_private_windows_are_never_recorded(scene, monkeypatch):
    scene.add(4, "Accounts - HDFC Bank - Google Chrome", "chrome.exe")
    scene.add(5, "New Incognito tab - Google Chrome (Incognito)", "chrome.exe")
    scene.add(6, "Pull requests - Google Chrome", "chrome.exe")
    urls = {4: "netbanking.hdfcbank.com/login", 5: "", 6: "github.com/pulls"}
    monkeypatch.setattr(activity, "read_url", lambda hwnd: urls.get(hwnd, ""))
    activity.save_prefs(on=True, blocked_sites=activity.DEFAULT_BLOCKED_SITES + ["github.com"])
    s = activity.Sampler(user_here=lambda now: True)
    _run(s, scene, [(4, 30), (5, 30), (6, 30), (1, 30)])
    assert [r["app"] for r in activity.spans()] == ["Code.exe"]
    assert activity.blocked("chrome.exe", "x", "api.github.com", activity.prefs())       # sub-domains too


@pytest.mark.parametrize("title, want", [
    ("Your OTP is 482913 - Messages", "(hidden)"),
    ("Reset your password - Google Chrome", "(hidden)"),
    ("Verification code - Microsoft account", "(hidden)"),
    ("Inbox (3) - asha.verma@example.com - Gmail", "Inbox (3) - (email) - Gmail"),
    ("Order 4111 1111 1111 1111 - Shop", "Order # - Shop"),
    ("main.py - nova - Visual Studio Code", "main.py - nova - Visual Studio Code"),
    ("PIN code finder - Google Chrome", "PIN code finder - Google Chrome"),
])
def test_secret_looking_titles_are_cleaned(title, want):
    assert activity.clean_title(title) == want


def test_a_secret_title_is_stored_cleaned(scene):
    scene.add(7, "Your OTP is 482913 - Messages", "PhoneExperienceHost.exe")
    activity.save_prefs(on=True)
    s = activity.Sampler(user_here=lambda now: True)
    _run(s, scene, [(7, 30)])
    assert [r["title"] for r in activity.spans()] == ["(hidden)"]
    assert "482913" not in activity.export_json()


def test_paused_records_nothing(scene):
    activity.save_prefs(on=True, paused=True)
    s = activity.Sampler(user_here=lambda now: True)
    _run(s, scene, [(1, 30), (2, 30)])
    assert s.state == "paused" and activity.spans() == []


def test_pausing_mid_span_closes_it_and_stops(scene):
    activity.save_prefs(on=True)
    s = activity.Sampler(user_here=lambda now: True)
    t = _run(s, scene, [(1, 30)])
    activity.save_prefs(paused=True)
    _run(s, scene, [(2, 60)], t0=t)
    assert [r["app"] for r in activity.spans()] == ["Code.exe"]


def test_only_while_the_user_is_here(scene):
    activity.save_prefs(on=True)
    here = {"v": False}
    s = activity.Sampler(user_here=lambda now: here["v"])
    t = _run(s, scene, [(1, 30)])
    assert s.state == "away" and activity.spans() == []
    here["v"] = True
    _run(s, scene, [(1, 30)], t0=t)
    assert len(activity.spans()) == 1


def test_without_presence_locked_or_idle_is_away(desktop):
    desktop.idle, desktop.locked = 5, False
    assert activity.default_here()
    desktop.idle = activity.IDLE_AWAY + 1
    assert not activity.default_here()
    desktop.idle, desktop.locked = 5, True
    assert not activity.default_here()


def test_nova_herself_and_blips_are_not_kept(scene):
    scene.add(8, "Nova", "pythonw.exe")
    activity.save_prefs(on=True)
    s = activity.Sampler(user_here=lambda now: True)
    _run(s, scene, [(8, 30), (2, 1.5 * 2), (1, 30)])          # Chrome for one tick only: alt-tab passing through
    assert [r["app"] for r in activity.spans()] == ["Code.exe"]


def test_old_spans_are_deleted_and_the_view_deletes(scene):
    now = 2_000_000_000.0
    old = activity.add_span(now - 31 * 86400, now - 31 * 86400 + 60, "Code.exe", "x", "")
    new = activity.add_span(now - 3600, now - 3500, "chrome.exe", "y", "github.com")
    assert activity.purge(now) == 1 and [r["id"] for r in activity.spans()] == [new]
    activity.add_span(now - 60, now - 30, "Code.exe", "z", "")
    assert activity.delete(app="github.com") == 1
    assert activity.delete(everything=True) == 1 and activity.spans() == [] and old


def test_the_api_switches_pauses_blocks_and_deletes(client, scene):
    j = client.post("/api/activity", json={"on": True}).get_json()
    assert j["on"] is True and j["paused"] is False
    activity.add_span(1000, 1100, "Spotify.exe", "Song", "")
    j = client.post("/api/activity", json={"block_app": "Spotify.exe"}).get_json()
    assert "spotify.exe" in j["blocked_apps"] and j["deleted"] == 1 and j["recent"] == []
    j = client.post("/api/activity", json={"paused": True}).get_json()
    assert j["paused"] is True and client.get("/api/activity/state").get_json()["paused"] is True


def test_the_tool_pauses_but_cannot_switch_on(scene):
    from nova.actions import tools
    assert "off" in tools.activity_log("pause") and activity.prefs()["paused"] is False
    activity.save_prefs(on=True)
    assert "Paused" in tools.activity_log("pause") and activity.prefs()["paused"] is True
    assert "recording again" in tools.activity_log("resume")
    assert "off" in tools.activity_log("off") and activity.prefs()["on"] is False


# ------------------------------------------------------------------ routines

def _day(date, start_hour, steps):
    """Spans for one day: steps [(app, host, minutes)] back to back from start_hour."""
    t = datetime.combine(date, datetime.min.time()) + timedelta(hours=start_hour)
    for app, host, mins in steps:
        s = t.timestamp()
        activity.add_span(s, s + mins * 60, app, "x", host)
        t += timedelta(minutes=mins, seconds=30)


MORNING = [("chrome.exe", "mail.google.com", 10), ("chrome.exe", "calendar.google.com", 3), ("Code.exe", "", 40)]


def test_a_routine_on_three_fake_days_is_found():
    monday = datetime(2026, 11, 2).date()                       # a Monday
    _day(monday, 9.0, MORNING)
    _day(monday + timedelta(days=1), 9.25, MORNING)
    _day(monday + timedelta(days=3), 8.75, [("Spotify.exe", "", 2)] + MORNING)
    _day(monday + timedelta(days=2), 15, [("Code.exe", "", 30), ("chrome.exe", "github.com", 10)])
    now = datetime(2026, 11, 9, 9, 5).timestamp()               # next Monday, 9:05
    found = activity.routines(now)
    assert len(found) == 1
    r = found[0]
    assert r["steps"] == ["Gmail", "Google Calendar", "VS Code"] and r["days"] == 3 and r["when"] == "weekdays"
    assert r["text"] == "weekdays ~9 am: Gmail → Google Calendar → VS Code"
    assert r["due"] is True and activity.routines(datetime(2026, 11, 9, 15, 0).timestamp())[0]["due"] is False


def test_two_days_or_scattered_times_are_not_a_routine():
    monday = datetime(2026, 11, 2).date()
    _day(monday, 9, MORNING)
    _day(monday + timedelta(days=1), 9, MORNING)
    _day(monday + timedelta(days=2), 18, MORNING)                # same steps, but in the evening
    assert activity.routines(datetime(2026, 11, 9, 9).timestamp()) == []


def test_routines_are_saved_as_unconfirmed_facts():
    monday = datetime(2026, 11, 2).date()
    for d in range(3):
        _day(monday + timedelta(days=d), 9, MORNING)
    now = datetime(2026, 11, 9, 12).timestamp()
    activity.save_routines(now)
    activity.save_routines(now)                                  # again: updated, not duplicated
    facts = [f for f in learner.load_facts()["facts"] if f.get("kind") == "routine"]
    assert len(facts) == 1 and facts[0]["confirmed"] is False
    assert learner.describe_fact(facts[0]).startswith("Your routine: weekdays ~9 am: Gmail")
    assert "x" not in json.dumps(facts[0]["steps"])               # no titles in the fact, only the step names
