"""The free-uses meter for the cloud brains (docs/plans/local-first-brain.md, Part A).

Google's free tier limits each model per project: requests per minute (RPM) and per day (RPD). Google publishes few
of the numbers and has no API that says how many are left, so Nova counts her own calls here and learns the limits
from Google's "limit reached" (429) answers, or from what the user types in the Brain tab.

When a model's limit is used up it is marked "out" until it comes back (midnight Pacific time for a daily limit, the
retry time Google gives for a per-minute one). While it is out, cloud.chat refuses at once without calling Google, so
llm.chat goes straight to the local brain instead of waiting for another failed call.

The free ladder: config/cloud_free.json lists each provider's free models from strongest to weakest ("ladder"). When
the chosen model is one of them and the ladder is on (the Brain tab's switch, kept in cloud_usage.json), every cloud
call goes to the strongest one that still has uses left; when Google says its limit is used up, cloud.chat moves down
to the next one in the same call. Only when every one of them is out does Nova use her local brain.

The counts live in data/cloud_usage.json. They are Nova's own count: AI Studio's rate-limit page is the real number
(other apps using the same Google project count there too).
"""
import calendar
import datetime as dt
import fnmatch
import json
import re
import threading
import time

from nova import shared
from nova import store

FREE_FILE = shared.ROOT / "config" / "cloud_free.json"
USAGE_NAME = "cloud_usage.json"
MINUTE = 60
DEFAULT_WAIT = 60           # a 429 that says nothing about when to retry: rest this long
STUDIO_URL = "https://aistudio.google.com/rate-limit"
_lock = threading.RLock()


# ------------------------------------------------------------------ Pacific time (Google's daily reset)
# Windows Python has no tz database (zoneinfo needs the tzdata package), so the US rule is written out:
# daylight time from the 2nd Sunday of March 02:00 to the 1st Sunday of November 02:00 (local).

def _nth_sunday(year, month, n):
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _pacific_offset(utc):
    """Hours to add to UTC for Pacific time at this UTC moment (-7 in summer, -8 in winter)."""
    start = dt.datetime.combine(_nth_sunday(utc.year, 3, 2), dt.time(10))   # 02:00 PST = 10:00 UTC
    end = dt.datetime.combine(_nth_sunday(utc.year, 11, 1), dt.time(9))     # 02:00 PDT = 09:00 UTC
    return -7 if start <= utc.replace(tzinfo=None) < end else -8


def pacific(now=None):
    """Pacific wall-clock time (naive datetime) for the epoch seconds `now`."""
    utc = dt.datetime.fromtimestamp(time.time() if now is None else now, dt.timezone.utc).replace(tzinfo=None)
    return utc + dt.timedelta(hours=_pacific_offset(utc))


def pacific_day(now=None):
    return pacific(now).date().isoformat()


def next_reset(now=None):
    """Epoch seconds of the next midnight Pacific time, when Google's daily limits start again."""
    now = time.time() if now is None else now
    midnight = dt.datetime.combine(pacific(now).date() + dt.timedelta(days=1), dt.time(0))
    guess = calendar.timegm(midnight.timetuple()) + 8 * 3600              # as if winter time
    return guess - 3600 if pacific(guess - 3600) >= midnight else guess   # summer time: an hour earlier


# ------------------------------------------------------------------ which models are free, and their limits

_free_cache = {"key": None, "conf": {}}


def _free_conf():
    """config/cloud_free.json (cached until it changes); {} when missing or broken, so a typo never stops Nova."""
    try:
        key = FREE_FILE.stat().st_mtime_ns
        if _free_cache["key"] != key:
            _free_cache.update(key=key, conf=json.loads(FREE_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        _free_cache.update(key=None, conf={})
    return _free_cache["conf"] if isinstance(_free_cache["conf"], dict) else {}


def kind(provider, name):
    """"free", "paid" or "" (not known) for a model, from config/cloud_free.json."""
    p = _free_conf().get(provider) or {}
    for k in ("free", "paid"):
        if any(fnmatch.fnmatchcase(name, pat) for pat in p.get(k) or []):
            return k
    return ""


def kinds(provider, names):
    return {n: kind(provider, n) for n in names}


def _key(provider, name):
    return f"{provider}:{name}"


def limits(provider, name, data=None):
    """{"rpd": int|None, "rpm": int|None}: what the user typed or Nova learned, else config/cloud_free.json."""
    data = data if data is not None else _load()
    base = ((_free_conf().get(provider) or {}).get("limits") or {}).get(name) or {}
    mine = (data.get("limits") or {}).get(_key(provider, name)) or {}
    out = {}
    for k in ("rpd", "rpm"):
        v = mine.get(k, base.get(k))
        out[k] = v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None
    return out


def ladder(provider):
    """The provider's free models, strongest first (config/cloud_free.json "ladder")."""
    rungs = (_free_conf().get(provider) or {}).get("ladder") or []
    return [n for n in rungs if isinstance(n, str) and n]


# ------------------------------------------------------------------ the counts

def _path():
    return store.current().file(USAGE_NAME)


def _load(now=None):
    """The meter, with today's counts (a new Pacific day starts them from 0; learned limits and a still-running
    "out" are kept)."""
    data = store.current().read_json(_path(), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("limits", {})
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    today, now = pacific_day(now), time.time() if now is None else now
    if data.get("day") != today:
        models = {k: {"out_until": m["out_until"], "why": m.get("why", "")} for k, m in models.items()
                  if isinstance(m, dict) and isinstance(m.get("out_until"), (int, float)) and m["out_until"] > now}
        data["day"] = today
    data["models"] = models
    return data


def _model(data, provider, name):
    m = data["models"].setdefault(_key(provider, name), {})
    for k, v in (("requests", 0), ("tokens", 0), ("recent", []), ("out_until", 0), ("why", "")):
        m.setdefault(k, v)
    return m


def record(provider, name, tokens=0, now=None):
    """One call reached the model (it answered, or failed for a reason other than its limit)."""
    now = time.time() if now is None else now
    with _lock:
        data = _load(now)
        m = _model(data, provider, name)
        m["requests"] += 1
        m["tokens"] += max(0, int(tokens or 0))
        m["recent"] = [t for t in m["recent"] if now - t < MINUTE] + [now]
        store.current().write_json(_path(), data)


def _limit_kind(detail, m, lim, now):
    """"day", "minute" or "" (can't tell) for a 429 message."""
    d = detail.lower()
    if re.search(r"per ?day|perday|daily|requests_per_day|_rpd\b", d):
        return "day"
    if re.search(r"per ?minute|perminute|_rpm\b|tokens per minute", d):
        return "minute"
    if lim["rpd"] and m["requests"] >= lim["rpd"]:
        return "day"
    if lim["rpm"] and len([t for t in m["recent"] if now - t < MINUTE]) >= lim["rpm"]:
        return "minute"
    return ""


def hit_limit(provider, name, detail="", now=None):
    """Google said 429 for this model: mark it out until it comes back, and learn the limit's number if the message
    gives it. Returns the epoch seconds it is out until."""
    now = time.time() if now is None else now
    detail = detail or ""
    with _lock:
        data = _load(now)
        m = _model(data, provider, name)
        lim = limits(provider, name, data)
        which = _limit_kind(detail, m, lim, now)
        retry = re.search(r"retry (?:in|after) ([\d.]+) ?s", detail, re.I) or re.search(r'"retryDelay": ?"([\d.]+)s"', detail)
        if which == "day":
            until = next_reset(now)
        else:
            until = now + (float(retry.group(1)) if retry else DEFAULT_WAIT)
        number = re.search(r"\blimit:? (\d+)", detail, re.I) or re.search(r'"quotaValue": ?"(\d+)"', detail)
        if number and which:
            data["limits"].setdefault(_key(provider, name), {}).setdefault("rpd" if which == "day" else "rpm",
                                                                          int(number.group(1)))
        m["out_until"] = max(m["out_until"], until)
        m["why"] = "daily" if which == "day" else "per-minute" if which == "minute" else ""
        store.current().write_json(_path(), data)
        return m["out_until"]


def set_limits(provider, name, rpd=None, rpm=None):
    """The numbers the user read on AI Studio's rate-limit page (None or 0 = not known)."""
    with _lock:
        data = _load()
        data["limits"][_key(provider, name)] = {k: int(v) for k, v in (("rpd", rpd), ("rpm", rpm)) if v}
        store.current().write_json(_path(), data)


def out_until(provider, name, now=None):
    """Epoch seconds this model is out until, or 0 when it can be used."""
    now = time.time() if now is None else now
    m = _load(now)["models"].get(_key(provider, name)) or {}
    t = m.get("out_until") or 0
    return t if t > now else 0


def blocked(provider, name, now=None):
    """The reason this model must not be called now ("" = go ahead)."""
    until = out_until(provider, name, now)
    if not until:
        return ""
    m = _load(now)["models"].get(_key(provider, name)) or {}
    what = {"daily": "free uses for today are", "per-minute": "free uses for this minute are"}.get(
        m.get("why"), "free-use limit is")
    return f"My {name} {what} used up, so I'm using my local brain until {time.strftime('%H:%M', time.localtime(until))}."


def ladder_on(data=None):
    data = data if data is not None else _load()
    return data.get("ladder", True) is not False


def set_ladder(on):
    with _lock:
        data = _load()
        data["ladder"] = bool(on)
        store.current().write_json(_path(), data)


def _rungs(provider, name, data):
    """The models the chosen `name` stands for: the whole ladder (strongest first) when it is a rung and the ladder is
    on, otherwise just itself."""
    rungs = ladder(provider)
    return rungs if name in rungs and ladder_on(data) else [name]


def route(provider, name, now=None):
    """The models to try, in order, for a call to the chosen model, leaving out the used-up ones ([] = all out)."""
    now = time.time() if now is None else now
    data = _load(now)
    return [n for n in _rungs(provider, name, data)
            if not ((data["models"].get(_key(provider, n)) or {}).get("out_until") or 0) > now]


def unavailable(provider, name, now=None):
    """Why the chosen model can't be used now ("" = at least one model it may use has uses left)."""
    now = time.time() if now is None else now
    if route(provider, name, now):
        return ""
    rungs = _rungs(provider, name, _load(now))
    if len(rungs) == 1:
        return blocked(provider, name, now)
    back = min(out_until(provider, n, now) for n in rungs)
    return (f"All my free {provider.title()} models are used up, so I'm using my local brain until "
            f"{time.strftime('%H:%M', time.localtime(back))}.")


def snapshot(provider=None, name=None, now=None):
    """What the Brain tab and the chip show: every model used today, plus the chosen one."""
    now = time.time() if now is None else now
    data = _load(now)
    keys = list(data["models"])
    rungs = _rungs(provider, name, data) if provider and name else []
    for n in reversed(rungs if len(rungs) > 1 else [name] if provider and name else []):
        if _key(provider, n) not in keys:
            keys.insert(0, _key(provider, n))
    if len(rungs) > 1:              # the ladder's rungs first, strongest first; other models used today after them
        keys.sort(key=lambda k: rungs.index(k.split(":", 1)[1]) if k in {_key(provider, n) for n in rungs} else len(rungs))
    live = route(provider, name, now) if provider and name else []
    rows = []
    for k in keys:
        p, n = k.split(":", 1)
        m = data["models"].get(k) or {}
        lim = limits(p, n, data)
        used = m.get("requests", 0)
        until = m.get("out_until", 0) if (m.get("out_until") or 0) > now else 0
        rows.append({"provider": p, "model": n, "kind": kind(p, n), "used": used, "tokens": m.get("tokens", 0),
                     "rpd": lim["rpd"], "rpm": lim["rpm"],
                     "left": max(0, lim["rpd"] - used) if lim["rpd"] else None,
                     "out_until": until, "why": m.get("why", "") if until else "",
                     "chosen": p == provider and n == name,
                     "rung": rungs.index(n) + 1 if p == provider and len(rungs) > 1 and n in rungs else None,
                     "current": bool(live) and p == provider and n == live[0]})
    return {"day": data["day"], "reset_at": next_reset(now), "now": now, "studio": STUDIO_URL, "models": rows,
            "ladder": {"on": ladder_on(data), "rungs": ladder(provider) if provider else [],
                       "in_use": len(rungs) > 1, "current": live[0] if live else None}}
