"""Light and hard jobs on the free ladder (local-first v2, L5): light jobs start at "light_from", hard ones at the top
(nova/brain/usage.py). Hermetic: no cloud call is made."""
import json

import pytest

from nova.brain import usage

LADDER = ["gemini-4-hydrogen", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3-flash-preview", "gemini-2.5-flash",
          "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]
TOP = LADDER[:5]                    # the 20-a-day models kept for hard work
LIGHT = LADDER[5:]
DAILY_429 = "Quota exceeded for metric: free_tier_requests per day, limit: 250. Please retry in 33s."


def own_config(monkeypatch, tmp_path, google):
    """Point usage at a cloud_free.json holding just this google section."""
    path = tmp_path / "cloud_free.json"
    path.write_text(json.dumps({"google": google}), encoding="utf-8")
    monkeypatch.setattr(usage, "FREE_FILE", path)
    monkeypatch.setattr(usage, "_free_cache", {"key": None, "conf": {}})


def test_the_shipped_config_starts_light_jobs_at_flash_lite():
    assert usage.light_from("google") == "gemini-3.5-flash-lite"
    assert usage.light_from("openai") == ""


def test_light_starts_at_flash_lite_and_hard_at_the_top():
    assert usage.route("google", "gemini-3.8-flash", job="light") == LIGHT
    assert usage.route("google", "gemini-3.8-flash", job="hard") == LADDER
    assert usage.route("google", "gemini-3.8-flash") == LADDER          # the default is hard: callers behave as today
    assert usage.route("google", "gemini-4-hydrogen", job="light")[0] == "gemini-3.5-flash-lite"


def test_a_used_up_rung_moves_down_for_both_kinds_of_job():
    usage.hit_limit("google", "gemini-4-hydrogen", DAILY_429)
    usage.hit_limit("google", "gemini-3.5-flash-lite", DAILY_429)
    assert usage.route("google", "gemini-3.8-flash", job="hard") == [n for n in LADDER if n not in
                                                                     {"gemini-4-hydrogen", "gemini-3.5-flash-lite"}]
    assert usage.route("google", "gemini-3.8-flash", job="light") == ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]


def test_light_never_climbs_into_the_top_models():
    for name in LIGHT:
        usage.hit_limit("google", name, DAILY_429)
    assert usage.route("google", "gemini-3.8-flash", job="light") == []     # top models still free, but not for light
    assert usage.route("google", "gemini-3.8-flash", job="hard") == TOP
    why = usage.unavailable("google", "gemini-3.8-flash", job="light")
    assert why.startswith("All my light Google models are used up")
    assert usage.unavailable("google", "gemini-3.8-flash", job="hard") == ""


def test_every_rung_used_up_fails_as_today_for_both_kinds():
    for name in LADDER:
        usage.hit_limit("google", name, DAILY_429)
    for job in ("hard", "light"):
        assert usage.route("google", "gemini-3.8-flash", job=job) == []
    assert usage.unavailable("google", "gemini-3.8-flash").startswith("All my free Google models are used up")


def test_no_light_from_behaves_as_today(monkeypatch, tmp_path):
    own_config(monkeypatch, tmp_path, {"free": LADDER, "ladder": LADDER})
    assert usage.light_from("google") == ""
    assert usage.route("google", "gemini-3.8-flash", job="light") == LADDER
    usage.hit_limit("google", "gemini-4-hydrogen", DAILY_429)
    assert usage.route("google", "gemini-3.8-flash", job="light") == LADDER[1:]


@pytest.mark.parametrize("bad", ["gemini-9-nope", 5, ""])
def test_a_light_from_that_is_not_a_rung_behaves_as_today(monkeypatch, tmp_path, bad):
    own_config(monkeypatch, tmp_path, {"free": LADDER, "ladder": LADDER, "light_from": bad})
    assert usage.route("google", "gemini-3.8-flash", job="light") == LADDER


def test_off_the_ladder_or_ladder_off_uses_only_the_chosen_model_for_light_jobs():
    assert usage.route("google", "gemini-2.5-pro", job="light") == ["gemini-2.5-pro"]
    usage.set_ladder(False)
    assert usage.route("google", "gemini-3.8-flash", job="light") == ["gemini-3.8-flash"]
