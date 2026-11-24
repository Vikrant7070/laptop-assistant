"""Local first v2, L6: the Brain tab's daily line, counted from task cards (nova/brainstats.py)."""
import datetime as dt
import time

from nova import brainstats, db, log, task_state as ts
from nova.brain import usage
from nova.core.app import resume

NOW = dt.datetime(2026, 11, 24, 15, 0).timestamp()          # local 3 pm
MIDNIGHT = dt.datetime(2026, 11, 24, 0, 0).timestamp()


def _card(ts_, tid, climbed="", checks=(), brain="qwen3-vl:4b", **extra):
    runs = [{"id": i + 1, "tool": "rename_file", "state": "done", "result": "ok", "step": None, "hint": "",
             "check": {"state": st, "why": f"why {i}", "target": f"t{i}"}} for i, st in enumerate(checks)]
    runs = runs or [{"id": 1, "tool": "get_system_status", "state": "done", "result": "ok", "step": None, "hint": ""}]
    card = {"id": tid, "goal": f"task {tid}", "status": "done", "started": ts_, "updated": ts_ + 30, "plan": [],
            "runs": runs, "brain": brain, **extra}
    if climbed:
        card["climbed"] = climbed
    return card


def _fixture():
    return [
        _card(NOW - 60, "a"),                                                     # local
        _card(NOW - 120, "b"),                                                    # local
        _card(NOW - 180, "c", climbed="3 steps with nothing new", checks=("passed",), brain="cloud (google)"),
        _card(NOW - 240, "d", climbed="it said it couldn't do it", checks=("passed", "failed"), brain="cloud (google)"),
        _card(NOW - 300, "e", climbed="8 steps and still not finished", brain="cloud (google)"),   # no read-back
        _card(NOW - 360, "f", climbed="x", brain="cloud (google)", rescued_by="gemini-4", hint_used=True,
              skill_offered=True),                                                # L2 fields
        _card(NOW - 420, "g", brain="cloud (google)"),                            # cloud first: not a climb
        {"id": "chat", "goal": "hi", "started": NOW - 10, "updated": NOW, "runs": [], "plan": []},   # not a task
    ]


def test_todays_line_counts_local_climbed_rescued_and_learned():
    out = brainstats.count(_fixture(), cloud_today=9, now=NOW)
    t = out["days"][0]
    assert (t["tasks"], t["local"], t["cloud"], t["climbed"], t["rescued"], t["not_rescued"]) == (7, 2, 1, 4, 2, 2)
    assert (t["hints"], t["skill_offers"], t["cloud_calls"]) == (1, 1, 9)
    assert t["line"] == ("Today: 7 tasks — 2 local, 1 on the cloud from the start, 4 climbed (2 rescued, 2 not). "
                         "Cloud calls: 9. Learned: 1 hint, 1 skill offer.")
    assert [c["id"] for c in out["climbs"]] == ["c", "d", "e", "f"]
    c = out["climbs"][0]
    assert c["why"] == "3 steps with nothing new" and c["rescued"] and c["link"] == "/tasks#t:c"
    assert not out["climbs"][1]["rescued"]          # one read-back still failing
    assert not out["climbs"][2]["rescued"]          # the Judge's "done" alone is not a rescue


def test_old_cards_without_l2_fields_count_zero_learned_and_find_the_climb_in_the_log():
    old = _card(NOW - 600, "old", checks=("passed",), brain="cloud (google)")        # saved before `climbed` was kept
    climbs = brainstats.climbs_from_events([
        {"ts": NOW - 590, "text": "CLIMB -> cloud:google:gemini-4: the same step 3 times with no change"},
        {"ts": NOW - 50, "text": "CLIMB refused (x): mail stays on this laptop"},
    ])
    out = brainstats.count([old], climbs, None, NOW)
    t = out["days"][0]
    assert (t["climbed"], t["rescued"], t["hints"], t["skill_offers"], t["cloud_calls"]) == (1, 1, 0, 0, None)
    assert out["climbs"][0]["why"] == "the same step 3 times with no change"
    assert "Cloud calls" not in t["line"] and t["line"].endswith("Learned: 0 hints, 0 skill offers.")


def test_seven_day_window_edges():
    first = (dt.datetime(2026, 11, 24) - dt.timedelta(days=6)).timestamp()       # 18 Nov, 00:00 local
    cards = [_card(first, "in"), _card(first - 1, "out"), _card(MIDNIGHT - 1, "yesterday"), _card(MIDNIGHT, "today"),
             _card(NOW + 3600, "future")]
    out = brainstats.count(cards, cloud_today=2, now=NOW)
    days = {d["day"]: d["tasks"] for d in out["days"]}
    assert len(out["days"]) == 7 and days["2026-11-18"] == 1 and days["2026-11-23"] == 1 and days["2026-11-24"] == 1
    assert out["week"]["tasks"] == 3
    assert out["days"][1]["line"].startswith("Yesterday: 1 task — 1 local, 0 climbed.")
    assert out["week"]["line"] == ("Last 7 days: 3 tasks — 3 local, 0 climbed. Cloud calls: 2 (today's only). "
                                   "Learned: 0 hints, 0 skill offers.")


def test_empty_day():
    assert brainstats.count([], now=NOW)["days"][0]["line"] == "Today: 0 tasks. Learned: 0 hints, 0 skill offers."


def test_resume_saves_the_climb_reason_on_the_card():
    task = {"goal": "rename report", "climbed": "3 steps with nothing new", "model": "cloud:google:gemini-4"}
    ts.attach(task)
    ts.record(task["card"], {"name": "get_system_status", "args": {}}, "ok", True)
    resume.save(task)
    saved = db.load_tasks()[0]
    assert saved["climbed"] == "3 steps with nothing new" and saved["brain"] == "cloud (google)"


def test_route_returns_the_line(client):
    now = time.time()
    db.save_task(_card(now - 5, "r1", climbed="8 steps and still not finished", checks=("passed",)))
    db.save_task(_card(now - 10, "r2"))
    log.event("log", "CLIMB -> cloud:google:gemini-4: 8 steps and still not finished")
    usage.record("google", "gemini-4")
    usage.record("google", "gemini-4")
    out = client.get("/api/brain/stats").get_json()
    assert out["days"][0]["line"] == ("Today: 2 tasks — 1 local, 1 climbed (1 rescued, 0 not). Cloud calls: 2. "
                                      "Learned: 0 hints, 0 skill offers.")
    assert out["week"]["line"].startswith("Last 7 days: 2 tasks")
    assert out["climbs"][0]["link"] == "/tasks#t:r1"
