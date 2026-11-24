"""Does local first get better? (local-first v2 plan, L6) - the Brain tab's daily line, counted from task cards.

    Today: 31 tasks - 27 local, 4 climbed (3 rescued, 1 not). Cloud calls: 9. Learned: 1 hint, 1 skill offer.

`count(cards, climbs, cloud_today, now)` is pure: it reads the saved task cards (nova.db's tasks table, the same
cards the /tasks page shows), the "CLIMB -> model: why" lines of the task log (for cards saved before the card
kept its own `climbed` reason), and today's count from the free-uses meter (nova/brain/usage.py). No new data file.

- A *task* is a card with steps or a plan (the /tasks page's rule: a chat with no steps isn't a task).
- *Climbed*: the local brain got stuck and the task went to the cloud brain (`card["climbed"]` = the reason, else a
  CLIMB line between the card's start and its last change). *Cloud* tasks that started on the cloud (cloud first)
  are counted apart, not as local.
- *Rescued*: a climbed task whose read-backs passed (at least one passed, none still failing), or one L2 marked
  with `rescued_by`. The Judge's "done" alone is not a rescue (plan L2: it runs on the local brain).
- *Learned*: cards L2 marks with `hint_used` / `skill_offered`. Old cards have neither and count 0.
- *Cloud calls*: the meter keeps only today (since midnight Pacific, Google's reset), so older days have none.

Days are local calendar days of the card's start; the week is today and the 6 days before it.
"""
import datetime as dt
import re

from nova import task_state

DAYS = 7
CLIMB_RE = re.compile(r"^CLIMB -> (\S+): (.+)$")


def _day(ts):
    return dt.datetime.fromtimestamp(ts).date()


def is_task(card):
    return bool(card.get("runs") or card.get("plan"))


def readbacks_passed(card):
    checks = [r["check"] for r in card.get("runs") or [] if isinstance(r, dict) and isinstance(r.get("check"), dict)]
    return any(c.get("state") == "passed" for c in checks) and not task_state.failing(card)


def climb_reason(card, climbs):
    """Why this card's task climbed, or "" when it didn't. `climbs`: [(ts, why)] from the task log."""
    if card.get("climbed"):
        return str(card["climbed"])
    if not str(card.get("brain") or "").startswith("cloud"):   # a climbed task ends on the cloud brain
        return ""
    start, end = float(card.get("started") or 0), float(card.get("updated") or 0)
    for ts, why in climbs:
        if start <= ts <= end + 1:
            return why
    return ""


def climbs_from_events(events):
    """[(ts, why)] from task-log events ({"ts", "text"}); "CLIMB refused" lines are not climbs."""
    out = []
    for e in events:
        m = CLIMB_RE.match(str(e.get("text") or ""))
        if m:
            out.append((float(e.get("ts") or 0), m.group(2).strip()))
    return out


def _empty(day):
    return {"day": day.isoformat(), "tasks": 0, "local": 0, "cloud": 0, "climbed": 0, "rescued": 0, "not_rescued": 0,
            "hints": 0, "skill_offers": 0, "cloud_calls": None}


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def line(label, c):
    """One day's (or the week's) sentence."""
    text = f"{label}: {_plural(c['tasks'], 'task')}"
    if c["tasks"]:
        parts = [f"{c['local']} local"]
        if c["cloud"]:
            parts.append(f"{c['cloud']} on the cloud from the start")
        parts.append(f"{c['climbed']} climbed" + (f" ({c['rescued']} rescued, {c['not_rescued']} not)" if c["climbed"] else ""))
        text += " — " + ", ".join(parts)
    text += "."
    if c["cloud_calls"] is not None:
        text += f" Cloud calls: {c['cloud_calls']}" + (" (today's only)" if c.get("calls_today_only") else "") + "."
    return text + f" Learned: {_plural(c['hints'], 'hint')}, {_plural(c['skill_offers'], 'skill offer')}."


def count(cards, climbs=(), cloud_today=None, now=0.0):
    """-> {"days": [today, yesterday, ... 7 days], "week": {...}, "climbs": [climbed tasks, newest first]}.

    Each day/week has the counts and its `line`; each climb has id, goal, why, when, rescued and its /tasks link."""
    today = _day(now)
    days = [_empty(today - dt.timedelta(days=i)) for i in range(DAYS)]
    by_day = {d["day"]: d for d in days}
    listed = []
    for card in cards:
        if not isinstance(card, dict) or not is_task(card):
            continue
        started = float(card.get("started") or 0)
        d = by_day.get(_day(started).isoformat()) if started <= now else None
        if d is None:
            continue
        d["tasks"] += 1
        why = climb_reason(card, climbs)
        if why:
            rescued = bool(card.get("rescued_by")) or readbacks_passed(card)
            d["climbed"] += 1
            d["rescued" if rescued else "not_rescued"] += 1
            listed.append({"id": str(card.get("id") or ""), "goal": task_state.clean(card.get("goal"), 120),
                           "why": task_state.clean(why, 120), "when": started, "day": d["day"], "rescued": rescued,
                           "rescued_by": task_state.clean(card.get("rescued_by") or "", 60),
                           "status": card.get("status") or "",
                           "link": "/tasks#t:" + str(card.get("id") or "")})
        elif str(card.get("brain") or "").startswith("cloud"):
            d["cloud"] += 1
        else:
            d["local"] += 1
        d["hints"] += bool(card.get("hint_used"))
        d["skill_offers"] += bool(card.get("skill_offered"))
    if cloud_today is not None:
        days[0]["cloud_calls"] = int(cloud_today)
    week = _empty(today)
    week["day"] = f"{days[-1]['day']}..{days[0]['day']}"
    for k in ("tasks", "local", "cloud", "climbed", "rescued", "not_rescued", "hints", "skill_offers"):
        week[k] = sum(d[k] for d in days)
    week["cloud_calls"], week["calls_today_only"] = days[0]["cloud_calls"], True
    for i, d in enumerate(days):
        d["line"] = line("Today" if i == 0 else "Yesterday" if i == 1 else _day_label(d["day"]), d)
    week["line"] = line("Last 7 days", week)
    listed.sort(key=lambda c: -c["when"])
    return {"days": days, "week": week, "climbs": listed}


def _day_label(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"
