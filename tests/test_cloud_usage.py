"""The free-uses meter for cloud brains (nova/brain/usage.py, docs/plans/local-first-brain.md Part A)."""
import calendar
import datetime as dt

import pytest

from nova import store
from nova.brain import cloud
from nova.brain import usage
from nova.core import app


def utc(*a):
    return calendar.timegm(dt.datetime(*a).timetuple())


REPLY = {"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 42}}
DAILY_429 = ('[{"error": {"code": 429, "message": "You exceeded your current quota. Quota exceeded for metric: '
             'generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 250, model: '
             'gemini-3.8-flash. Please retry in 33s.", "details": [{"violations": [{"quotaId": '
             '"GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "250"}]}]}}]')
MINUTE_429 = ('{"error": {"message": "Quota exceeded for quota metric requests per minute, limit: 10. '
              'Please retry in 21.5s."}}')


@pytest.mark.parametrize("now, day, reset", [
    (utc(2026, 11, 23, 12), "2026-11-23", utc(2026, 11, 24, 8)),     # winter: 04:00 PST -> midnight = 08:00 UTC
    (utc(2026, 11, 23, 5), "2026-11-22", utc(2026, 11, 23, 8)),      # 21:00 PST the day before
    (utc(2026, 7, 1, 12), "2026-07-01", utc(2026, 7, 2, 7)),         # summer: midnight PDT = 07:00 UTC
    (utc(2026, 10, 31, 20), "2026-10-31", utc(2026, 11, 1, 7)),      # the night clocks go back
    (utc(2026, 11, 1, 12), "2026-11-01", utc(2026, 11, 2, 8)),
    (utc(2026, 3, 7, 20), "2026-03-07", utc(2026, 3, 8, 8)),         # the night clocks go forward
    (utc(2026, 3, 8, 12), "2026-03-08", utc(2026, 3, 9, 7)),
])
def test_pacific_day_and_reset(now, day, reset):
    assert usage.pacific_day(now) == day
    assert usage.next_reset(now) == reset


def test_counts_requests_and_tokens_and_starts_fresh_each_pacific_day():
    t = utc(2026, 11, 23, 12)
    # set_limits reads the meter with the real clock, which starts a fresh day on any other date than t's and drops
    # the counts below (the test only passed on 2026-11-23): set the limit first.
    usage.set_limits("google", "gemini-3.8-flash", rpd=250)
    usage.record("google", "gemini-3.8-flash", 100, now=t)
    usage.record("google", "gemini-3.8-flash", 20, now=t + 5)
    row = usage.snapshot("google", "gemini-3.8-flash", now=t + 10)["models"][0]
    assert (row["used"], row["tokens"], row["left"], row["kind"]) == (2, 120, 248, "free")
    nxt = usage.snapshot("google", "gemini-3.8-flash", now=utc(2026, 11, 24, 9))["models"][0]   # after midnight PST
    assert (nxt["used"], nxt["left"]) == (0, 250)           # counts start again; the limit is kept


def test_daily_429_marks_the_model_out_until_midnight_pacific_and_learns_the_limit():
    t = utc(2026, 11, 23, 12)
    until = usage.hit_limit("google", "gemini-3.8-flash", DAILY_429, now=t)
    assert until == utc(2026, 11, 24, 8)
    assert usage.limits("google", "gemini-3.8-flash")["rpd"] == 250
    assert "used up" in usage.blocked("google", "gemini-3.8-flash", now=t + 3600)
    assert usage.blocked("google", "gemini-3.8-flash", now=until + 1) == ""      # back after the reset


def test_per_minute_429_waits_only_the_retry_time():
    t = utc(2026, 11, 23, 12)
    assert usage.hit_limit("google", "gemini-3.8-flash", MINUTE_429, now=t) == pytest.approx(t + 21.5)
    assert usage.limits("google", "gemini-3.8-flash")["rpm"] == 10
    assert usage.limits("google", "gemini-3.8-flash")["rpd"] is None


def test_unreadable_429_rests_a_minute_and_learns_nothing():
    t = utc(2026, 11, 23, 12)
    assert usage.hit_limit("google", "gemini-3.8-flash", "Resource exhausted.", now=t) == t + usage.DEFAULT_WAIT
    assert usage.limits("google", "gemini-3.8-flash") == {"rpd": None, "rpm": None}


def test_an_out_model_stays_out_across_the_pacific_day_change():
    t = utc(2026, 11, 24, 7, 59, 50)
    usage.hit_limit("google", "gemini-3.8-flash", MINUTE_429, now=t)      # out until 08:00:11.5, after midnight PST
    assert usage.out_until("google", "gemini-3.8-flash", now=utc(2026, 11, 24, 8, 0, 10))


def test_free_and_paid_models():
    assert usage.kind("google", "gemini-3.8-flash") == "free"
    assert usage.kind("google", "gemma-4-27b-it") == "free"
    assert usage.kind("google", "gemini-4-argon") == "paid"
    assert usage.kind("google", "gemini-9-unknown") == ""
    assert usage.kind("openai", "gpt-6-mini") == "paid"


def test_corrupt_meter_file_starts_from_zero():
    store.current().file(usage.USAGE_NAME).write_text("{not json", encoding="utf-8")
    assert usage.snapshot("google", "gemini-3.8-flash")["models"][0]["used"] == 0
    usage.record("google", "gemini-3.8-flash")
    assert usage.snapshot()["models"][0]["used"] == 1


# ------------------------------------------------------------------ cloud.chat feeds the meter

@pytest.fixture
def google_key():
    cloud.set_key("google", "test-key")
    yield
    cloud.set_key("google", "")


def test_a_reply_counts_one_use_with_its_tokens(monkeypatch, google_key):
    monkeypatch.setattr(cloud, "_http", lambda *a, **k: REPLY)
    cloud.chat("cloud:google:gemini-3.8-flash", [{"role": "user", "content": "hi"}], [])
    row = usage.snapshot()["models"][0]
    assert (row["model"], row["used"], row["tokens"]) == ("gemini-3.8-flash", 1, 42)


def test_a_429_marks_out_and_the_next_call_never_reaches_google(monkeypatch, google_key):
    calls = []

    def limited(*a, **k):
        calls.append(1)
        raise cloud.CloudError("limit (429)", 429, DAILY_429)
    monkeypatch.setattr(cloud, "_http", limited)
    with pytest.raises(cloud.CloudError):
        cloud.chat("cloud:google:gemini-3.8-flash", [{"role": "user", "content": "hi"}], [])
    assert len(calls) == 1 and usage.out_until("google", "gemini-3.8-flash")
    with pytest.raises(cloud.CloudError, match="used up"):
        cloud.chat("cloud:google:gemini-3.8-flash", [{"role": "user", "content": "hi"}], [])
    assert len(calls) == 1                                   # refused at once, no second call
    assert usage.snapshot()["models"][0]["used"] == 0        # a 429 isn't a use
    assert "CLOUD LIMIT google:gemini-3.8-flash" in store.current().task_log.read_text(encoding="utf-8")


def test_no_internet_is_not_counted(monkeypatch, google_key):
    monkeypatch.setattr(cloud, "_http", lambda *a, **k: (_ for _ in ()).throw(cloud.CloudError("no internet")))
    with pytest.raises(cloud.CloudError):
        cloud.chat("cloud:google:gemini-3.8-flash", [{"role": "user", "content": "hi"}], [])
    assert usage.snapshot()["models"] == []


# ------------------------------------------------------------------ the API the Brain tab uses

def _use_gemini():
    app.config.save_settings(dict(app.config.settings(), provider="google", cloud_models={"google": "gemini-3.8-flash"}))


def test_usage_api_features_the_chosen_model(client):
    _use_gemini()
    j = client.get("/api/cloud/usage").get_json()
    assert j["active"] is True and j["studio"].startswith("https://aistudio.google.com")
    row = j["models"][0]
    assert (row["model"], row["kind"], row["used"], row["chosen"]) == ("gemini-3.8-flash", "free", 0, True)


def test_limits_api_saves_numbers_and_rejects_text(client):
    _use_gemini()
    r = client.post("/api/cloud/limits", json={"provider": "google", "model": "gemini-3.8-flash", "rpd": 250, "rpm": 10})
    assert r.status_code == 200 and r.get_json()["models"][0]["left"] == 250
    assert client.post("/api/cloud/limits", json={"provider": "google", "model": "gemini-3.8-flash",
                                                  "rpd": "lots"}).status_code == 400
    assert client.post("/api/cloud/limits", json={"provider": "nope", "model": "x"}).status_code == 400
    r = client.post("/api/cloud/limits", json={"provider": "google", "model": "gemini-3.8-flash", "rpd": None})
    assert r.get_json()["models"][0]["left"] is None         # cleared = not known
