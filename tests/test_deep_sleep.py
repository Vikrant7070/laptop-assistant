"""Asleep for 10 minutes -> deep sleep: her mic turns off completely. A click on her (or a typed message) wakes her,
turns the mic back on, and she plays her wake-up move."""
import time

import pytest

from nova.core import app


@pytest.fixture
def body(monkeypatch):
    sent = []
    monkeypatch.setattr(app.avatar_client, "send", lambda do, timeout=20, **a: sent.append((do, a)))
    return sent


@pytest.fixture
def ears(monkeypatch):
    calls = []
    monkeypatch.setattr(app.ears, "start", lambda: calls.append("start"))
    monkeypatch.setattr(app.ears, "stop", lambda: calls.append("stop"))
    return calls


@pytest.fixture(autouse=True)
def fresh_nap():
    app.wake_body()
    app.nap.pop("deep", None)
    yield
    app.nap["deep"] = False
    app.wake_body()


def asleep_for(secs):
    app.sleep_body()
    app.nap["since"] = time.time() - secs


def test_not_deep_until_ten_minutes_asleep(body, ears):
    asleep_for(app.DEEP_SLEEP_AFTER - 30)
    assert not app.deep_sleep_due()
    app.nap["since"] = time.time() - app.DEEP_SLEEP_AFTER - 1
    assert app.deep_sleep_due()


def test_deep_sleep_turns_the_mic_off(body, ears):
    asleep_for(app.DEEP_SLEEP_AFTER + 1)
    app.go_deep()
    assert ears == ["stop"] and app.nap["deep"]
    assert ("sleep", {"on": True, "deep": True}) in body
    assert not app.deep_sleep_due()           # only once


def test_deep_sleep_can_be_turned_off(body, ears, monkeypatch):
    s = dict(app.settings(), deep_sleep=False)
    monkeypatch.setattr(app, "settings", lambda: s)
    asleep_for(app.DEEP_SLEEP_AFTER + 60)
    assert not app.deep_sleep_due()


def test_click_wakes_her_and_turns_the_mic_back_on(body, ears, monkeypatch):
    s = dict(app.settings(), wake=True)
    monkeypatch.setattr(app, "settings", lambda: s)
    asleep_for(app.DEEP_SLEEP_AFTER + 1)
    app.go_deep()
    r = app.app.test_client().post("/api/wake").get_json()
    assert r["was_asleep"] and not app.nap["asleep"] and not app.nap["deep"]
    assert ears == ["stop", "start"]
    assert ("sleep", {"on": False, "clicked": True}) in body


def test_mic_stays_off_on_waking_if_hands_free_is_off(body, ears, monkeypatch):
    s = dict(app.settings(), wake=False)
    monkeypatch.setattr(app, "settings", lambda: s)
    asleep_for(app.DEEP_SLEEP_AFTER + 1)
    app.go_deep()
    app.note_task()                            # a typed message wakes her too
    assert ears == ["stop"] and not app.nap["asleep"]


def test_falling_asleep_again_restarts_the_deep_sleep_clock(body, ears):
    asleep_for(app.DEEP_SLEEP_AFTER + 1)
    app.sleep_body()                           # already asleep: the clock keeps counting
    assert app.deep_sleep_due()
    app.wake_body()
    app.sleep_body()
    assert not app.deep_sleep_due()
