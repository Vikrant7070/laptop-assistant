"""Suggestions at the right moment (manager plan Phase 8, the safe part). Pure rules: no brain, no screen, no keys.

Phase 7 finds the user's routines by plain code ("weekdays ~9 am: Gmail -> Calendar -> VS Code") and gives them as
`routines(now=None)`. Here:

    found_routines(now)                 Phase 7's routines through a small adapter -> [routine]; [] when Phase 7
                                        isn't there yet or it fails (logged)
    pick(routines, now, ctx, settings)  the one routine to offer now, or (None, why)
    prepare_calls(routine)              the routine's steps that may run AHEAD of its time: level 0 (read only) AND on
                                        a short allow-list. Nothing level 1 or 2 ever runs without the user's tap.
    request(routine)                    the words a Yes runs, as if the user had said them ("Open Gmail, then ...")
    shown / answer / never / snooze     what was offered and what the user said: data/suggestions.json

A routine (after the adapter): {"id", "name", "steps": [{"label", "tool", "args"}], "minute": minutes after
midnight, "days": [0-6, Monday = 0] or None for every day}.

The rules (the user's, Phase 8 task): offered once a day per routine, inside its usual time +- WINDOW_MIN, only while
the user is at the laptop and nothing else is going on (no task running, no question open, nothing full screen, no
meeting, no game), never in quiet hours, at most `routine_suggest_max` a day. "Not now" = not that routine again
today, and no suggestion at all for SNOOZE_MIN. "Don't suggest this" = never again (until the user clears it).
"""
import datetime as dt
import importlib
import json
import re
import threading
import time

from nova import store
from nova.actions import risk
from nova.log import log_exception

WINDOW_MIN = 20                 # offered from 20 minutes before the usual time to 20 minutes after it
PREP_MIN = 15                   # prepare-ahead may start this much before the offer window opens
SNOOZE_MIN = 60                 # "Not now": no suggestion at all for an hour
FILE = "suggestions.json"
SOURCES = ("nova.actions.activity", "nova.actions.routine_log")     # where Phase 7's routines(now=None) may live
PREPARE_OK = {"check_email", "get_datetime", "get_system_status", "list_reminders", "list_notes", "network_info"}
PREPARE_FOR = {"gmail": ("check_email", {}), "mail": ("check_email", {}), "outlook": ("check_email", {}),
               "inbox": ("check_email", {})}          # an app in the routine -> a read that can be ready ahead
DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
CHOICES = (("yes", "Yes"), ("later", "Not now"), ("never", "Don't suggest this"))
_lock = threading.RLock()

# Not now: a call or meeting in front (Teams / Zoom / Meet / Webex). The title must say so: Teams open is not a call.
MEETING_PROCS = {"teams.exe", "ms-teams.exe", "zoom.exe", "cpthost.exe", "webex.exe", "webexmta.exe", "slack.exe"}
MEETING_TITLE = re.compile(r"\b(meeting|call|huddle|webinar)\b|^meet\s*[-–]|\bzoom\b|google meet", re.I)


# ------------------------------------------------------------------ Phase 7's routines (the adapter)

def source():
    """Phase 7's routines(now=None), or None while it isn't on this branch."""
    for name in SOURCES:
        try:
            mod = importlib.import_module(name)
        except ModuleNotFoundError as e:
            if e.name == name:
                continue
            raise
        fn = getattr(mod, "routines", None)
        if callable(fn):
            return fn
    return None


def found_routines(now=None):
    """Phase 7's routines, normalised. [] when there are none, Phase 7 isn't there, or it fails (logged)."""
    try:
        fn = source()
        if fn is None:
            return []
        raw = fn(now=now)
    except Exception:
        log_exception("suggest: reading Phase 7's routines")
        return []
    out = []
    for r in raw or []:
        n = normalise(r)
        if n:
            out.append(n)
    return out


def _clean(text, n):
    line = " ".join(str(text or "").split())
    return line if len(line) <= n else line[: n - 1].rstrip() + "…"


def _minute(r):
    """Minutes after midnight from "time": "9:05" / "09:05", "minute_of_day": 545, or "hour": 9.25."""
    t = r.get("time")
    if isinstance(t, str):
        m = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*$", t)
        if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
            return int(m.group(1)) * 60 + int(m.group(2))
    for key, scale in (("minute_of_day", 1), ("hour", 60)):
        v = r.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v * scale < 1440:
            return int(round(v * scale))
    return None


def _days(v):
    """[0-6] / ["mon", ...] / "weekdays" / "weekends" / "daily" -> a sorted list, or None (every day)."""
    if v in (None, "", "daily", "every day", "everyday"):
        return None
    if v == "weekdays":
        return [0, 1, 2, 3, 4]
    if v in ("weekends", "weekend"):
        return [5, 6]
    out = set()
    for d in v if isinstance(v, (list, tuple, set)) else [v]:
        if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6:
            out.add(d)
        elif isinstance(d, str) and d[:3].lower() in DAYS:
            out.add(DAYS[d[:3].lower()])
    return sorted(out) or None


def normalise(r):
    """One routine from Phase 7 -> our shape, or None when it has no steps or no time."""
    if not isinstance(r, dict):
        return None
    steps = []
    for s in r.get("steps") or r.get("apps") or r.get("sequence") or []:
        if isinstance(s, str):
            label, tool, args = s, "open_app", {"name": s}
        elif isinstance(s, dict):
            label = s.get("label") or s.get("app") or s.get("name") or s.get("tool") or ""
            tool = str(s.get("tool") or "open_app")
            args = s.get("args") if isinstance(s.get("args"), dict) else ({"name": label} if tool == "open_app" else {})
        else:
            continue
        label = _clean(label, 40)
        if label:
            steps.append({"label": label, "tool": tool, "args": dict(args)})
    minute = _minute(r)
    if not steps or minute is None:
        return None
    name = _clean(r.get("name") or r.get("label") or "", 60) or "your usual setup"
    rid = _clean(r.get("id") or r.get("key") or name + ": " + " > ".join(s["label"] for s in steps), 120)
    return {"id": rid, "name": name, "steps": steps[:8], "minute": minute, "days": _days(r.get("days"))}


# ------------------------------------------------------------------ the words

def chain(routine):
    return " → ".join(s["label"] for s in routine["steps"])


def question_text(routine):
    """"Open your morning setup? (Gmail → Calendar → VS Code)"."""
    name = routine["name"]
    name = name if name.lower().startswith(("your ", "my ")) else "your " + name
    return f"Open {name.removeprefix('my ')}? ({chain(routine)})"


def why(routine):
    hm = f"{routine['minute'] // 60}:{routine['minute'] % 60:02d}"
    return f"You usually do this around {hm}. Each step still asks as usual; nothing has run yet."


def request(routine):
    """What a Yes runs, as the user's own words: the normal path plans it and asks by risk level."""
    parts = []
    for s in routine["steps"]:
        parts.append(f"open {s['label']}" if s["tool"] == "open_app" else s["label"])
    text = ", then ".join(parts)
    return text[:1].upper() + text[1:]


# ------------------------------------------------------------------ the moment

def _hm(text, default):
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*$", str(text or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m and int(m.group(1)) < 24 and int(m.group(2)) < 60 else default


def valid_quiet(text):
    a, _, b = str(text).partition("-")
    return _hm(a, None) is not None and _hm(b, None) is not None


def quiet_now(settings, now):
    """In the quiet hours setting ("22:00-07:00"; "" = none)?"""
    q = str(settings.get("routine_quiet") or "").strip()
    if "-" not in q:
        return False
    a, b = q.split("-", 1)
    start, end = _hm(a, None), _hm(b, None)
    if start is None or end is None or start == end:
        return False
    m = now.hour * 60 + now.minute
    return start <= m < end if start < end else (m >= start or m < end)


def off_by(routine, now):
    """Minutes from the routine's usual time today (negative = before), or None on a day it doesn't run."""
    if routine["days"] is not None and now.weekday() not in routine["days"]:
        return None
    return now.hour * 60 + now.minute - routine["minute"]


def in_window(routine, now):
    d = off_by(routine, now)
    return d is not None and abs(d) <= WINDOW_MIN


def blocked(ctx):
    """Why now is a bad moment, or "" (ctx from the app: present, busy, question, fullscreen, front title/proc)."""
    if not ctx.get("present", False):
        return "the user isn't at the laptop"
    if ctx.get("busy"):
        return "a task is running"
    if ctx.get("asleep"):
        return "Nova is asleep"
    if ctx.get("question"):
        return "a question is already open"
    if ctx.get("fullscreen"):
        return f"something is full screen ({ctx['fullscreen']})"
    proc, title = str(ctx.get("proc") or "").lower(), str(ctx.get("title") or "")
    if (proc in MEETING_PROCS and MEETING_TITLE.search(title)) or proc == "cpthost.exe" or \
            re.search(r"^meet\s*[-–]|google meet|zoom meeting", title, re.I):
        return "a meeting or call is in front"
    return ""


def pick(routines, now, ctx, settings, state=None):
    """-> (routine, "") to offer now, or (None, why not)."""
    if not settings.get("routine_suggest", True):
        return None, "suggestions are off"
    state = state if state is not None else load(now)
    if quiet_now(settings, now):
        return None, "quiet hours"
    limit = int(settings.get("routine_suggest_max", 2) or 0)
    if len(state["shown"]) >= limit:
        return None, f"the daily limit ({limit}) is used"
    if now.timestamp() < state["snooze_all"]:
        return None, "snoozed"
    why_not = blocked(ctx)
    if why_not:
        return None, why_not
    shown = {s["id"] for s in state["shown"]}
    for r in sorted(routines, key=lambda r: abs(off_by(r, now) or 0)):
        if r["id"] in state["never"] or r["id"] in shown or not in_window(r, now):
            continue
        return r, ""
    return None, "no routine due"


# ------------------------------------------------------------------ prepare ahead (level 0 only)

def prepare_calls(routine):
    """The reads that may run before the user says Yes: an allow-listed tool that is level 0 (risk.LEVELS). A routine
    step that changes anything (level 1 or 2), a shell command, or any tool not on the list is never in here."""
    calls = []
    for s in routine["steps"]:
        if s["tool"] != "open_app":
            calls.append((s["tool"], dict(s["args"])))
        hit = PREPARE_FOR.get(s["label"].lower())
        if hit:
            calls.append((hit[0], dict(hit[1])))
    out, seen = [], set()
    for tool, args in calls:
        if tool in PREPARE_OK and tool not in risk.SHELL and risk.level(tool, args) == risk.READ and tool not in seen:
            seen.add(tool)
            out.append((tool, args))
    return out


def prepare_due(routine, now, state=None):
    """Prepare-ahead runs once a day, from PREP_MIN before the offer window opens until the window closes."""
    state = state if state is not None else load(now)
    d = off_by(routine, now)
    return (d is not None and -(WINDOW_MIN + PREP_MIN) <= d <= WINDOW_MIN and routine["id"] not in state["never"]
            and routine["id"] not in state["prepared"])


# ------------------------------------------------------------------ data/suggestions.json

def _blank(day):
    return {"version": 1, "day": day, "shown": [], "prepared": [], "never": [], "snoozed": [], "snooze_all": 0.0,
            "answers": []}


def _path():
    return store.current().file(FILE)


def load(now=None):
    """Today's state (a new day clears shown / prepared / snoozed; "never" and the snooze time stay)."""
    now = now or dt.datetime.now()
    day = now.strftime("%Y-%m-%d")
    with _lock:
        try:
            d = json.loads(_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            d = _blank(day)
        except Exception:
            log_exception("suggestions read")
            d = _blank(day)
        for k, v in _blank(day).items():
            d.setdefault(k, v)
        if d["day"] != day:
            d.update(day=day, shown=[], prepared=[], snoozed=[])
        return d


def _save(d):
    with _lock:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)


def mark_shown(rid, now=None):
    now = now or dt.datetime.now()
    with _lock:
        d = load(now)
        d["shown"].append({"id": rid, "at": now.timestamp()})
        _save(d)


def mark_prepared(rid, now=None):
    now = now or dt.datetime.now()
    with _lock:
        d = load(now)
        if rid not in d["prepared"]:
            d["prepared"].append(rid)
            _save(d)


def answer(rid, choice, name="", now=None):
    """yes / later / never (or "dropped": the card went unanswered - it still counts as today's offer)."""
    now = now or dt.datetime.now()
    with _lock:
        d = load(now)
        if choice == "later":
            d["snoozed"].append(rid)
            d["snooze_all"] = now.timestamp() + SNOOZE_MIN * 60
        elif choice == "never" and rid not in d["never"]:
            d["never"].append(rid)
            d.setdefault("never_names", {})[rid] = _clean(name, 80)
        d["answers"] = (d["answers"] + [{"id": rid, "choice": choice, "at": now.timestamp()}])[-50:]
        _save(d)


def snooze(minutes, now=None):
    now = now or dt.datetime.now()
    with _lock:
        d = load(now)
        d["snooze_all"] = now.timestamp() + max(0, int(minutes)) * 60
        _save(d)


def allow_again(rid=None):
    """Clear one "Don't suggest this" (or all of them)."""
    with _lock:
        d = load()
        d["never"] = [] if rid is None else [x for x in d["never"] if x != rid]
        names = d.get("never_names", {})
        d["never_names"] = {} if rid is None else {k: v for k, v in names.items() if k != rid}
        _save(d)


def summary(now=None):
    """For the settings page: today's count, the snooze, and the routines the user said never to suggest."""
    d = load(now)
    names = d.get("never_names", {})
    return {"shown_today": len(d["shown"]), "snoozed_until": d["snooze_all"] if d["snooze_all"] > time.time() else 0,
            "never": [{"id": rid, "name": names.get(rid) or rid} for rid in d["never"]]}
