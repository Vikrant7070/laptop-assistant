"""Scheduled and repeating jobs (manager plan 4g): the rules and the record. Pure code, no app imports.

    parse(words)        -> {"every", "request", "guessed_time"} or None: "every morning at 9 check my Gmail and tell me
                           what's important", "every Sunday copy Documents to D:", "every minute check the time"
    steps(request)      -> [{"text", "tool", "level"}]: the request split into steps, each with its risk level from
                           risk.level (a guess from the words - every real step is checked again when it runs)
    describe(every)     -> "every day at 09:00"
    next_after(every, t) -> the first run time after t (local time)
    command(words)      -> ("pause" | "resume" | "delete", n) or ("list", None) or None

The record is data/schedules.json (store.current().schedules): a list of
    {"id", "request", "words", "every", "steps", "made", "source", "paused", "next", "waiting", "runs": [...]}
Each run: {"due", "at", "late", "status" (done / blocked / failed), "task" (its task card's id), "said"}.

Only the user's own words make a schedule: app.scheduling.route reads the request the user typed, said or sent
from the paired phone. No tool makes one, so text inside an email or a web page never can (and 4e's outside-order
rule quotes "every morning at 9 delete …" found there).
"""
import re
import time
from datetime import datetime, timedelta

from nova import store
from nova.actions import risk

MAX_RUNS = 10                 # runs kept per schedule
MIN_MINUTES = 1
DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
PART_OF_DAY = {"morning": "09:00", "afternoon": "14:00", "evening": "18:00", "night": "21:00", "noon": "12:00",
               "midnight": "00:00"}
DEFAULT_AT = "09:00"
LEVEL_WORDS = {0: "level 0 - only reads, runs freely", 1: "level 1 - changes something, shown, Undo where it can",
               2: "level 2 - asks you every time it runs"}

_DAY = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
_DAYS = rf"{_DAY}s?(?:\s*(?:,|and|&)\s*{_DAY}s?)*"
_EVERY = re.compile(
    r"\b(?:(?:every|each)\s+(?P<n>\d+)\s*(?P<unit>minutes?|mins?|hours?|hrs?)"
    r"|(?:every|each)\s+(?P<one>minute|hour)"
    r"|(?P<hourly>hourly)"
    rf"|(?:(?:every|each)\s+|on\s+(?=(?:{_DAY}s)))(?P<days>{_DAYS})(?:\s+(?P<dpart>morning|afternoon|evening|night))?"
    r"|(?:every|each)\s+(?P<wk>weekday|weekend)s?(?:\s+(?P<wpart>morning|afternoon|evening|night))?"
    r"|(?:every|each)\s+(?P<part>morning|afternoon|evening|night)"
    r"|(?:every|each)\s+(?P<day>day|week)|(?P<daily>daily)|(?P<weekly>weekly))\b", re.I)
_AT = re.compile(r"\b(?:at|@)\s*(?:(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)?(?!\s*(?:%|gb|mb|kb))"
                 r"|(?P<word>noon|midnight))(?![\w:])", re.I)
_TRIM = re.compile(r"^(?:\W|please\b|and\b|then\b|,)+|(?:[\s,;]+(?:please|and|then))+\W*$|[\s,;]+$", re.I)
_REMIND = re.compile(r"^(?:please\s+)?remind me$", re.I)
_CALLED = re.compile(r"^(?:hey\s+|ok\s+)?\w+\s*,\s*(?:please\s+)?(?=(?:every|each|on|at|daily|hourly|weekly)\b)", re.I)
_QUESTION = re.compile(r"^(?:what|how|why|when|which|who|did|do|does|is|are|was|were|have|has|can you tell)\b", re.I)
_STEP_SPLIT = re.compile(
    r"\s*(?:;|,\s*(?:and\s+)?(?:then\s+)?|\s+and\s+then\s+|\s+then\s+|\s+and\s+(?=(?:tell|send|copy|move|check|open|"
    r"read|delete|email|write|save|close|remind|show|let|give|list|make|put|rename|clean|empty|play|turn|set|install|"
    r"run|search|find|look|back|summari[sz]e|reply|forward|post|download)\b))", re.I)
# A step's words -> the tool that would most likely do it (first match wins). None: just her answer (level 0).
STEP_TOOLS = [
    (r"\bempty\b.*\b(recycle|bin|trash)", "empty_recycle_bin"),
    (r"\b(delete|remove|erase|wipe)\b", "delete_to_recycle_bin"),
    (r"\bclean\b.*\btemp", "clean_temp_files"),
    (r"\b(send|reply|forward|post|publish|share|text|message)\b|\bemail\b(?!s)\s+\w", "send_message"),
    (r"\b(pay|buy|purchase|order|transfer)\b", "pay"),
    (r"\b(install|uninstall|update)\b", "install_app"),
    (r"\b(shut ?down|restart|reboot)\b", "power_action"),
    (r"\b(run|execute)\b.*\b(command|script|powershell|terminal)\b", "run_powershell"),
    (r"\b(sign|log) ?in\b", "login_if_needed"),
    (r"\b(copy|back ?up)\b", "copy_path"),
    (r"\b(move|rename|organi[sz]e|tidy|sort)\b", "move_or_rename"),
    (r"\b(write|save|note down|draft)\b", "write_text_file"),
    (r"\b(open|start|launch)\b", "open_app"),
    (r"\bclose\b", "window_action"),
    (r"\b(mail|gmail|inbox|emails)\b", "check_email"),
    (r"\b(research|look up|search the web|news)\b", "web_research"),
    (r"\b(find|search)\b", "search_files"),
    (r"\b(battery|cpu|memory|disk|system|laptop status)\b", "get_system_status"),
    (r"\b(time|date)\b", "get_datetime"),
    (r"\b(remind)\b", "set_reminder"),
    (r"\b(check|read|look|tell|show|list|summari[sz]e|what|how|give me|let me know)\b", None),
]
STEP_TOOLS = [(re.compile(rx, re.I), tool) for rx, tool in STEP_TOOLS]
_COMMAND = re.compile(r"^\W*(?P<verb>pause|stop|resume|unpause|restart|start|delete|remove|cancel)\s+(?:the\s+|my\s+)?"
                      r"(?:scheduled?\s*(?:job)?|repeating job)\s*(?:#|no\.?|number)?\s*(?P<n>\d+)\W*$", re.I)
_LIST = re.compile(r"^\W*(?:(?:what|which)\s+(?:are\s+)?(?:my\s+)?schedules?|(?:list|show)\s+(?:me\s+)?(?:my\s+)?"
                   r"(?:schedules?|scheduled jobs|repeating jobs)|my\s+schedules?)\W*$", re.I)
VERBS = {"pause": "pause", "stop": "pause", "resume": "resume", "unpause": "resume", "restart": "resume",
         "start": "resume", "delete": "delete", "remove": "delete", "cancel": "delete"}


# ------------------------------------------------------------------ reading the user's words

def _hhmm(h, m=0):
    return f"{int(h) % 24:02d}:{int(m):02d}"


def _at(m, part):
    """A matched "at 9" / "at 6:30 pm" / "at noon" -> "HH:MM" (a part of day turns "at 7" in the evening into 19:00)."""
    if m.group("word"):
        return PART_OF_DAY[m.group("word").lower()]
    h, mins, ap = int(m.group("h")), int(m.group("m") or 0), (m.group("ap") or "").lower().replace(".", "")
    if h > 23 or mins > 59:
        return None
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    elif not ap and part in ("afternoon", "evening", "night") and h < 12:
        h += 12
    return _hhmm(h, mins)


def _blank(text):
    for _ in range(3):
        text = _TRIM.sub("", text.strip()).strip()
    return not text


def _at_an_end(text, spans):
    """The times start or end the request ("every Sunday copy …", "check my mail every morning at 9"), or follow
    "remind me" - not in the middle of something else."""
    every, times = spans[0], spans[1:]
    for a, b in times:                               # "at 9" may sit on either side: blank it out, same length
        text = text[:a] + " " * (b - a) + text[b:]
    head, tail = text[:every[0]], text[every[1]:]
    return _blank(head) or _blank(tail) or bool(_REMIND.match(head.strip()))


def parse(words):
    """The user's words -> {"every": {...}, "request": "…", "guessed_time": bool}, or None when they don't ask for a
    repeating job (or there is nothing to do once the times are taken out)."""
    text = _CALLED.sub("", re.sub(r"\s+", " ", str(words or "")).strip())     # "Nova, every minute …"
    m = _EVERY.search(text)
    if not m:
        return None
    g = {k: (v.lower() if isinstance(v, str) else v) for k, v in m.groupdict().items()}
    spans = [m.span()]
    if g["n"] or g["one"] or g["hourly"]:
        n = int(g["n"] or 1)
        unit = g["unit"] or g["one"] or "hour"
        every = {"kind": "minutes", "n": n * 60 if unit.startswith(("hour", "hr")) or g["hourly"] else n}
        if every["n"] < MIN_MINUTES:
            return None
        guessed = False
    else:
        part = g["dpart"] or g["wpart"] or g["part"]
        if g["days"]:
            days = sorted({DAY_NAMES.index(re.sub(r"s$", "", d.lower())) for d in re.findall(_DAY + "s?", g["days"], re.I)})
        elif g["wk"] == "weekday":
            days = [0, 1, 2, 3, 4]
        elif g["wk"] == "weekend":
            days = [5, 6]
        elif g["day"] == "week" or g["weekly"]:
            days = [datetime.now().weekday()]
        else:
            days = list(range(7))
        at, guessed = None, False
        t = _AT.search(text)
        if t:
            at = _at(t, part)
            if not at:
                return None                          # "at 25": not a time - let her ask
            spans.append(t.span())
        if not at:
            at, guessed = PART_OF_DAY.get(part or "", DEFAULT_AT), not part
        every = {"kind": "days", "days": days, "at": at}
    if not _at_an_end(text, spans):
        return None                                  # "open the file I edit every day": not a schedule
    rest = text
    for a, b in sorted(spans, reverse=True):
        rest = rest[:a] + " " + rest[b:]
    rest = re.sub(r"\s+", " ", rest).strip()
    for _ in range(3):
        rest = _TRIM.sub("", rest).strip()
    rest = rest.strip().rstrip(" .,;!")
    if len(rest.split()) < 2 or _QUESTION.match(rest):
        return None
    return {"every": every, "request": rest[0].upper() + rest[1:], "guessed_time": guessed}


def steps(request):
    """The request split into steps, each with the tool it would most likely use and that tool's risk level."""
    out = []
    for part in _STEP_SPLIT.split(str(request or "")):
        part = part.strip(" .")
        if not part:
            continue
        tool = next((t for rx, t in STEP_TOOLS if rx.search(part)), "?")
        if tool == "?":
            tool, lvl = None, risk.CHANGE           # can't tell from the words: never 0 on a guess
        elif tool is None:
            lvl = risk.READ                         # look / tell me: her answer
        else:
            lvl = risk.HARD if tool in risk.SHELL else risk.level(tool)
        out.append({"text": part[:200], "tool": tool or "", "level": lvl})
    return out


def describe(every):
    if every["kind"] == "minutes":
        n = every["n"]
        if n % 60 == 0:
            return "every hour" if n == 60 else f"every {n // 60} hours"
        return "every minute" if n == 1 else f"every {n} minutes"
    days = sorted(every["days"])
    if days == list(range(7)):
        which = "every day"
    elif days == [0, 1, 2, 3, 4]:
        which = "every weekday"
    elif days == [5, 6]:
        which = "every weekend day"
    else:
        names = [DAY_NAMES[d].capitalize() for d in days]
        which = "every " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1])
    return f"{which} at {every['at']}"


def next_after(every, t):
    """The first run time strictly after t (seconds since the epoch, local clock)."""
    if every["kind"] == "minutes":
        return float(t) + every["n"] * 60
    h, m = (int(x) for x in every["at"].split(":"))
    base = datetime.fromtimestamp(t)
    for d in range(8):
        when = (base + timedelta(days=d)).replace(hour=h, minute=m, second=0, microsecond=0)
        if when.weekday() in every["days"] and when.timestamp() > t:
            return when.timestamp()
    return float(t) + 86400                         # unreachable with at least one day


def command(words):
    """ "pause schedule #3" / "resume schedule 3" / "delete schedule #3" / "what are my schedules" -> (verb, n)."""
    text = str(words or "").strip()
    m = _COMMAND.match(text)
    if m:
        return VERBS[m.group("verb").lower()], int(m.group("n"))
    if _LIST.match(text):
        return "list", None
    return None


def when_text(ts):
    """A run time for people: "today 09:00", "tomorrow 09:00", "Sun 29 Nov 09:00"."""
    d = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    if d.date() == today:
        day = "today"
    elif d.date() == today + timedelta(days=1):
        day = "tomorrow"
    else:
        day = d.strftime("%a %d %b")
    return f"{day} {d.strftime('%H:%M')}"


# ------------------------------------------------------------------ the record

def _path():
    return store.current().schedules


def load():
    data = store.current().read_json(_path(), [])
    return [r for r in data if isinstance(r, dict) and "id" in r] if isinstance(data, list) else []


def _save(recs):
    store.current().write_json(_path(), recs)


def get(n):
    return next((r for r in load() if r["id"] == n), None)


def add(plan, words, source="laptop", now=None):
    """Save a schedule the user said yes to. -> the record."""
    now = time.time() if now is None else now
    with store.current().lock:
        recs = load()
        rec = {"id": max((r["id"] for r in recs), default=0) + 1, "request": plan["request"],
               "words": str(words)[:300], "every": plan["every"], "steps": steps(plan["request"]), "made": now,
               "source": source, "paused": False, "next": next_after(plan["every"], now), "waiting": None, "runs": []}
        recs.append(rec)
        _save(recs)
    return rec


def change(n, fn):
    """Apply fn(record) to schedule n and save. -> the record after, or None when there is no such schedule."""
    with store.current().lock:
        recs = load()
        rec = next((r for r in recs if r["id"] == n), None)
        if rec is None:
            return None
        fn(rec)
        _save(recs)
        return rec


def set_paused(n, paused, now=None):
    def apply(rec):
        rec["paused"] = bool(paused)
        rec["waiting"] = None
        if not paused:                               # resumed: the next time from now, not the ones missed meanwhile
            rec["next"] = next_after(rec["every"], time.time() if now is None else now)
    return change(n, apply)


def delete(n):
    with store.current().lock:
        recs = load()
        keep = [r for r in recs if r["id"] != n]
        if len(keep) == len(recs):
            return False
        _save(keep)
        return True


def due(now=None):
    """Schedules whose time has come (not paused), the earliest first."""
    now = time.time() if now is None else now
    return sorted((r for r in load() if not r.get("paused") and r.get("next", 0) <= now), key=lambda r: r["next"])


def is_late(rec, now, late_secs):
    return now - rec["next"] > late_secs
