"""📊 Activity log, level 1 (manager plan Phase 7): which app and window the user has in front, and when.

OFF until the user switches it on (⚙ Privacy). While on, every few seconds it notes only:
    the app (process name), the window in front (its title, cleaned), the site's host for browsers, start / end time.
Never keys, never typed text, never screenshots, never the clipboard. Everything stays in <data>/activity.db on this
laptop; raw spans are deleted after KEEP_DAYS. Nothing here is sent to any brain: what leaves this module is the
routines found by plain code ("weekdays ~9 am: Gmail → Calendar → VS Code"), saved as unconfirmed facts the user
can confirm or delete on the Knowledge page.

It records only while the user is the one at the laptop (the app passes `user_here`: presence (YuNet) when that is
on, otherwise "unlocked and touched in the last few minutes"), never in a blocked app or site (password managers,
banking, private windows by default), and never while paused.

    sampler = activity.Sampler(user_here=lambda now: True)
    sampler.tick()                 # every TICK seconds, from the app's watcher thread
    activity.recent(), activity.delete(ids), activity.routines(now)

Form noticing (forms.notice) rides on the same tick: see Sampler._forms.
"""
import json
import re
import sqlite3
import statistics
import threading
import time
import urllib.parse
from datetime import datetime

from nova import platform
from nova import store
from nova.log import log_exception

TICK = 3.0                    # seconds between samples
KEEP_DAYS = 30                # raw spans older than this are deleted
IDLE_AWAY = 300               # presence off: no keyboard / mouse this long = not at the laptop
FLUSH_SECS = 30               # an open span's end time is written at least this often (a crash loses at most this)
MIN_SPAN = 2.0                # shorter than this (alt-tab passing through) is not kept
SETTINGS_FILE = "activity.json"
DB_FILE = "activity.db"

BROWSERS = {"chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe", "vivaldi.exe", "arc.exe"}
NOVA_TITLES = {"nova", "nova 3d", "nova ear", "tk"}
DEFAULT_BLOCKED_APPS = [
    "1password.exe", "bitwarden.exe", "keepass.exe", "keepassxc.exe", "lastpass.exe", "dashlane.exe", "enpass.exe",
    "nordpass.exe", "roboform.exe", "proton pass.exe", "credentialuibroker.exe", "consent.exe", "lockapp.exe",
]
DEFAULT_BLOCKED_SITES = [
    "bank", "netbanking", "paypal.com", "paytm.com", "phonepe.com", "pay.google.com", "razorpay.com",
    "zerodha.com", "groww.in", "upstox.com", "1password.com", "bitwarden.com", "lastpass.com", "passwords.google.com",
    "accounts.google.com", "login.microsoftonline.com", "incometax.gov.in", "uidai.gov.in", "passportindia.gov.in",
]
DEFAULTS = {"on": False, "paused": False, "notice_forms": True,
            "blocked_apps": DEFAULT_BLOCKED_APPS, "blocked_sites": DEFAULT_BLOCKED_SITES}

# A private / incognito window: never recorded, whatever the site.
PRIVATE_TITLE = re.compile(r"\b(incognito|inprivate|private browsing)\b", re.I)
# Titles about money or secrets: the whole window is left out (banks put the bank's name in the title).
BLOCKED_TITLE = re.compile(r"\b(net\s*banking|internet banking|online banking|bank account|upi pin|"
                           r"password manager|1password|bitwarden|keepass)\b", re.I)
# SECRETY for titles: a title that mentions a code or password is kept only as "(hidden)". Not plain "code" or
# "pin": "Visual Studio Code" and "PIN code" (a postal code) are everyday titles.
SECRET_TITLE = re.compile(r"\b(otp|one[- ]time|verification|passcode|password|passwd|cvv|security code|login code|"
                          r"sign[- ]?in code|auth(entication)? code|2fa|two[- ]factor|recovery code|reset your)\b", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
DIGITS = re.compile(r"\d[\d\s-]{3,}\d")         # 5+ digits in a row (with spaces / dashes): codes, card, phone numbers

_lock = threading.RLock()


# ------------------------------------------------------------------ settings (data/activity.json)

def prefs():
    d = store.current().read_json(store.current().file(SETTINGS_FILE), {}) or {}
    out = {**DEFAULTS, **{k: v for k, v in d.items() if k in DEFAULTS}}
    for k in ("on", "paused", "notice_forms"):
        out[k] = bool(out[k])
    for k in ("blocked_apps", "blocked_sites"):
        out[k] = [str(x).strip().lower() for x in (out[k] if isinstance(out[k], list) else DEFAULTS[k]) if str(x).strip()]
    return out


def save_prefs(**changes):
    """Change some settings -> the new settings. Lists are cleaned (lower case, no blanks, at most 200)."""
    with _lock:
        s = prefs()
        for k, v in changes.items():
            if k not in DEFAULTS:
                raise KeyError(k)
            if k in ("blocked_apps", "blocked_sites"):
                v = [str(x).strip().lower() for x in (v or []) if str(x).strip()][:200]
            else:
                v = bool(v)
            s[k] = v
        store.current().write_json(store.current().file(SETTINGS_FILE), s)
        return s


# ------------------------------------------------------------------ cleaning and blocking (pure)

def clean_title(title):
    """The title as it may be kept: a secret-looking title becomes "(hidden)"; e-mail addresses and long numbers
    inside an ordinary one are taken out."""
    t = " ".join(str(title or "").split())[:300]
    if not t:
        return ""
    if SECRET_TITLE.search(t):
        return "(hidden)"
    t = EMAIL.sub("(email)", t)
    return DIGITS.sub("#", t)


def host_of(url):
    """'https://www.github.com/x' or 'github.com/x' (what an address bar shows) -> 'github.com'."""
    u = str(url or "").strip()
    if not u or " " in u:
        return ""
    if "://" not in u:
        u = "http://" + u
    try:
        h = (urllib.parse.urlsplit(u).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def site_blocked(host, blocked):
    """A blocked entry with a dot is a domain (the host or any of its sub-domains); without one it is a word."""
    h = str(host or "").lower()
    if not h:
        return False
    for b in blocked:
        if "." in b:
            if h == b or h.endswith("." + b):
                return True
        elif b in h:
            return True
    return False


def blocked(app, title, host, s):
    """Is this window never to be recorded? (blocked app, private window, blocked site, money / password title)"""
    if str(app or "").lower() in s["blocked_apps"]:
        return True
    if PRIVATE_TITLE.search(title or "") or BLOCKED_TITLE.search(title or ""):
        return True
    return site_blocked(host, s["blocked_sites"])


# ------------------------------------------------------------------ the log (data/activity.db)

def _connect():
    con = sqlite3.connect(str(store.current().file(DB_FILE)), timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS spans (id INTEGER PRIMARY KEY AUTOINCREMENT, start REAL NOT NULL, "
                "end REAL NOT NULL, app TEXT, title TEXT, host TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS spans_start ON spans(start)")
    return con


def _run(sql, args=(), fetch=False):
    with _lock:
        store.current().root.mkdir(parents=True, exist_ok=True)
        con = _connect()
        try:
            with con:
                cur = con.execute(sql, args)
                return cur.fetchall() if fetch else cur.lastrowid
        finally:
            con.close()


def add_span(start, end, app, title, host):
    """One span (also used by tests to make fake days). -> its id."""
    return _run("INSERT INTO spans (start, end, app, title, host) VALUES (?, ?, ?, ?, ?)",
                (float(start), float(end), app, title, host))


def _set_end(sid, end):
    _run("UPDATE spans SET end = ? WHERE id = ?", (float(end), sid))


def spans(since=0.0, until=None, limit=None):
    """Spans as dicts, oldest first."""
    sql, args = "SELECT id, start, end, app, title, host FROM spans WHERE start >= ?", [float(since)]
    if until is not None:
        sql += " AND start < ?"
        args.append(float(until))
    sql += " ORDER BY start"
    rows = _run(sql, args, fetch=True)
    if limit:
        rows = rows[-int(limit):]
    return [{"id": r[0], "start": r[1], "end": r[2], "app": r[3], "title": r[4], "host": r[5]} for r in rows]


def recent(limit=200):
    """The newest spans first (the ⚙ view)."""
    return list(reversed(spans(limit=limit)))


def delete(ids=None, everything=False, app=None, since=None, until=None):
    """Delete spans: some ids, everything, everything of one app / site, or a time range. -> how many went."""
    with _lock:
        before = _run("SELECT COUNT(*) FROM spans", fetch=True)[0][0]
        if everything:
            _run("DELETE FROM spans")
        elif ids:
            for sid in ids:
                _run("DELETE FROM spans WHERE id = ?", (int(sid),))
        elif app:
            _run("DELETE FROM spans WHERE lower(app) = ? OR lower(host) = ?", (str(app).lower(), str(app).lower()))
        elif since is not None:
            _run("DELETE FROM spans WHERE start >= ? AND start < ?", (float(since), float(until or 1e12)))
        return before - _run("SELECT COUNT(*) FROM spans", fetch=True)[0][0]


def purge(now=None, keep_days=KEEP_DAYS):
    """Raw spans older than keep_days go. -> how many."""
    cut = (now or time.time()) - keep_days * 86400
    with _lock:
        n = _run("SELECT COUNT(*) FROM spans WHERE end < ?", (cut,), fetch=True)[0][0]
        if n:
            _run("DELETE FROM spans WHERE end < ?", (cut,))
        return n


# ------------------------------------------------------------------ sampling

def _uia_url(hwnd):
    """A browser window's address (its address bar, read through UI Automation). "" if it can't be read."""
    try:
        import uiautomation as auto
        from nova.actions.recorder import ADDRESS_RE
        bar = auto.ControlFromHandle(hwnd).EditControl(searchDepth=14, RegexName="(?i).*(" + ADDRESS_RE.pattern + ")")
        if not bar.Exists(0, 0):
            return ""
        return (bar.GetValuePattern().Value or "").strip()
    except Exception:
        return ""


def _uia_focus():
    """The focused text box as (box id, name, value) - None for anything else, and never a password box's value."""
    try:
        import uiautomation as auto
        c = auto.GetFocusedControl()
        if c is None or c.ControlTypeName not in ("EditControl", "ComboBoxControl") or c.IsPassword:
            return None
        return (tuple(c.GetRuntimeId() or ()), c.Name or "", c.GetValuePattern().Value or "")
    except Exception:
        return None


read_url = _uia_url           # tests put fakes here
read_focus = _uia_focus


def default_here(now=None):
    """Presence off: the user is at the laptop while it is unlocked and they touched it in the last few minutes."""
    d = platform.current()
    return not d.screen_locked() and d.idle_seconds() < IDLE_AWAY


class Sampler:
    """One sample per tick(): extends the open span or starts a new one. All decisions in plain code.

    user_here(now) -> bool, on_form_offer(keys) -> None (the app asks the question card)."""

    def __init__(self, user_here=None, on_form_offer=None):
        self.user_here = user_here or (lambda now: default_here(now))
        self.on_form_offer = on_form_offer
        self.open = None          # {"id", "key", "start", "end", "flushed"}
        self.procs = {}           # hwnd -> process name (looked up once per window)
        self.hosts = {}           # (hwnd, title) -> host
        self.state = "off"        # off | paused | away | blocked | recording - the indicator shows it
        self.last_purge = 0.0
        self.box = None           # the form box in focus: (id, name, value) - in memory only
        self.form_title = ""      # the title of the page that box was on
        self.ticks = 0

    # -- the open span
    def _close(self, now):
        o, self.open = self.open, None
        if not o:
            return
        if o["end"] - o["start"] < MIN_SPAN:
            delete(ids=[o["id"]])
        else:
            _set_end(o["id"], o["end"])

    def _note(self, key, now):
        o = self.open
        if o and o["key"] == key:
            o["end"] = now
            if now - o["flushed"] >= FLUSH_SECS:
                _set_end(o["id"], now)
                o["flushed"] = now
            return
        self._close(now)
        app, title, host = key
        self.open = {"id": add_span(now, now, app, title, host), "key": key, "start": now, "end": now, "flushed": now}

    def stop(self, now=None):
        """Close the open span (switching off, pausing, quitting)."""
        self._close(now or time.time())
        self.box, self.form_title = None, ""

    # -- what is in front
    def _proc(self, hwnd):
        if hwnd not in self.procs:
            if len(self.procs) > 300:
                self.procs.clear()
            self.procs[hwnd] = next((w.proc for w in platform.current().windows() if w.hwnd == hwnd), "")
        return self.procs[hwnd]

    def _host(self, hwnd, title, app):
        if app.lower() not in BROWSERS:
            return ""
        k = (hwnd, title)
        if k not in self.hosts:
            if len(self.hosts) > 500:
                self.hosts.clear()
            self.hosts[k] = host_of(read_url(hwnd))
        return self.hosts[k]

    def tick(self, now=None):
        """One sample. Never raises (a bad sample is logged and skipped)."""
        now = now or time.time()
        try:
            self.ticks += 1
            self._tick(now)
        except Exception:
            log_exception("activity: a sample")
        return self.state

    def _tick(self, now):
        if now - self.last_purge > 3600:
            self.last_purge = now
            purge(now)
        s = prefs()
        if not s["on"] or s["paused"]:
            self.state = "off" if not s["on"] else "paused"
            self.stop(now)
            return
        if not self.user_here(now):
            self.state = "away"
            self.stop(now)
            return
        d = platform.current()
        hwnd = d.foreground()
        raw = d.title(hwnd) if hwnd else ""
        if not hwnd or not raw or raw.strip().lower() in NOVA_TITLES:
            self.state = "recording"
            self._close(now)
            return
        app = self._proc(hwnd) or "?"
        host = self._host(hwnd, raw, app)
        if blocked(app, raw, host, s):
            self.state = "blocked"           # nothing about this window is kept - not even that it was blocked
            self._close(now)
            self.box = None
            return
        self.state = "recording"
        self._note((app, clean_title(raw), host), now)
        if s["notice_forms"] and app.lower() in BROWSERS:        # web forms only: one cheap UI Automation call
            self._forms(raw, now)

    # -- form noticing: the user typed their name / email / phone / address into a form box
    def _forms(self, raw_title, now):
        """While a form box that asks for a profile detail (forms.classify) has the focus, keep its value in memory;
        when the focus leaves it, hand it to forms.notice. When the page moves on (the title changes: submitted) and
        something new was seen, offer to save it - once. Password boxes are never read (read_focus skips them)."""
        from nova.actions import forms
        f = read_focus()
        prev = self.box
        if f and forms.notice.wanted(f[1]):
            if prev and prev[0] != f[0]:
                forms.notice.seen(prev[1], prev[2])
            self.box = (f[0], f[1], f[2])
            self.form_title = raw_title
            return
        if prev:
            forms.notice.seen(prev[1], prev[2])
            self.box = None
        if self.form_title and raw_title != self.form_title:     # the page moved on (sent) or they went elsewhere
            self.form_title = ""
            keys = forms.notice.offer(now)
            if keys and self.on_form_offer:
                self.on_form_offer(keys)


# ------------------------------------------------------------------ routines (plain code)

HOST_NAMES = {"mail.google.com": "Gmail", "calendar.google.com": "Google Calendar", "docs.google.com": "Google Docs",
              "drive.google.com": "Google Drive", "meet.google.com": "Google Meet", "web.whatsapp.com": "WhatsApp",
              "github.com": "GitHub", "youtube.com": "YouTube", "chatgpt.com": "ChatGPT", "claude.ai": "Claude",
              "outlook.office.com": "Outlook", "outlook.live.com": "Outlook", "linkedin.com": "LinkedIn",
              "news.google.com": "Google News", "x.com": "X", "reddit.com": "Reddit"}
APP_NAMES = {"code.exe": "VS Code", "outlook.exe": "Outlook", "olk.exe": "Outlook", "winword.exe": "Word",
             "excel.exe": "Excel", "powerpnt.exe": "PowerPoint", "explorer.exe": "File Explorer",
             "windowsterminal.exe": "Terminal", "spotify.exe": "Spotify", "ms-teams.exe": "Teams", "teams.exe": "Teams",
             "whatsapp.exe": "WhatsApp", "notepad.exe": "Notepad", "slack.exe": "Slack", "discord.exe": "Discord",
             "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox", "brave.exe": "Brave",
             "hxcalendarappimm.exe": "Calendar", "claude.exe": "Claude", "obsidian.exe": "Obsidian"}
MIN_DAYS = 3                  # a sequence seen on this many different days...
WINDOW_MIN = 90               # ...starting within this many minutes of each other (time of day) is a routine
STEP_SECS = 20                # a span shorter than this is passing through, not a step
GAP_MIN = 20                  # steps further apart than this are not one sequence
MAX_LEN = 4


def label(span):
    """What a step is called in a routine: the site for a browser, else the app."""
    h = span.get("host") or ""
    if h:
        return HOST_NAMES.get(h) or next((n for k, n in HOST_NAMES.items() if h.endswith("." + k)), h)
    app = str(span.get("app") or "").lower()
    return APP_NAMES.get(app) or re.sub(r"\.exe$", "", app).capitalize() or "?"


def _steps_by_day(rows):
    """{day: [(minute of day, label, start ts, end ts)]} with passing-through spans dropped and repeats merged."""
    days = {}
    for r in rows:
        if r["end"] - r["start"] < STEP_SECS:
            continue
        dt = datetime.fromtimestamp(r["start"])
        day = dt.date()
        lab = label(r)
        steps = days.setdefault(day, [])
        if steps and steps[-1][1] == lab and r["start"] - steps[-1][3] < GAP_MIN * 60:
            steps[-1] = (steps[-1][0], lab, steps[-1][2], max(steps[-1][3], r["end"]))
        else:
            steps.append((dt.hour * 60 + dt.minute, lab, r["start"], r["end"]))
    return days


def _when(days):
    wd = {d.weekday() for d in days}
    if wd <= {0, 1, 2, 3, 4}:
        return "weekdays", [0, 1, 2, 3, 4]
    if wd <= {5, 6}:
        return "weekends", [5, 6]
    return "most days", list(range(7))


def _clock(minute):
    h, m = divmod(int(minute), 60)
    m = int(round(m / 15.0)) * 15
    if m == 60:
        h, m = h + 1, 0
    ap = "am" if h % 24 < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}{':%02d' % m if m else ''} {ap}"


def find_routines(rows, min_days=MIN_DAYS):
    """Spans (dicts) -> routines, most days first. Pure: tests feed fake days."""
    seen = {}                                           # sequence -> [(day, minute)]
    for day, steps in _steps_by_day(rows).items():
        for i in range(len(steps)):
            for n in range(2, MAX_LEN + 1):
                part = steps[i:i + n]
                if len(part) < n:
                    break
                if any(b[2] - a[3] > GAP_MIN * 60 for a, b in zip(part, part[1:])):
                    break
                seq = tuple(p[1] for p in part)
                if len(set(seq)) < len(seq):
                    continue                            # A → B → A: going back and forth, not a routine
                seen.setdefault(seq, []).append((day, part[0][0]))
    found = []
    for seq, occ in seen.items():
        occ.sort(key=lambda o: o[1])
        best = []
        for i, (_, m0) in enumerate(occ):              # the time-of-day window holding the most different days
            inside = {}
            for d, m in occ[i:]:
                if m - m0 > WINDOW_MIN:
                    break
                inside.setdefault(d, m)
            if len(inside) > len(best):
                best = list(inside.items())
        if len(best) < min_days:
            continue
        days = sorted(d for d, _ in best)
        minute = statistics.median(m for _, m in best)
        when, weekdays = _when(days)
        found.append({"key": "routine:" + " > ".join(seq), "steps": list(seq), "minute": minute,
                      "window": [max(0, minute - WINDOW_MIN // 2), min(24 * 60, minute + WINDOW_MIN // 2)],
                      "when": when, "weekdays": weekdays, "days": len(days), "last_day": days[-1].isoformat(),
                      "text": f"{when} ~{_clock(minute)}: " + " → ".join(seq)})
    # a shorter sequence inside a longer one found on as many days is the same routine
    found.sort(key=lambda r: (-len(r["steps"]), -r["days"]))
    keep = []
    for r in found:
        if any(_inside(r["steps"], k["steps"]) and k["days"] >= r["days"] and abs(k["minute"] - r["minute"]) <= WINDOW_MIN
               for k in keep):
            continue
        keep.append(r)
    return sorted(keep, key=lambda r: (-r["days"], r["minute"]))


def _inside(short, long_):
    n = len(short)
    return any(long_[i:i + n] == short for i in range(len(long_) - n + 1))


def routines(now=None, days=KEEP_DAYS):
    """The routines found in the last `days` days (Phase 8 reads this). Each: {"key", "text", "steps", "minute",
    "window" [from, to] minutes of the day, "when", "weekdays" [0=Mon..], "days" seen, "last_day", "due"} - "due" is
    True when `now` falls in its window on one of its weekdays. Empty when the log is off and was never on."""
    now = now or time.time()
    out = find_routines(spans(since=now - days * 86400, until=now))
    dt = datetime.fromtimestamp(now)
    minute = dt.hour * 60 + dt.minute
    for r in out:
        r["due"] = dt.weekday() in r["weekdays"] and r["window"][0] <= minute <= r["window"][1]
    return out


def save_routines(now=None):
    """Found routines -> unconfirmed facts (kind "routine", one per sequence; the Knowledge page shows them with ✓ and
    delete). A routine the user deleted comes back only if it is still being seen. -> the facts added or updated."""
    from nova.actions import learner
    now = now or time.time()
    found = routines(now)
    out = []
    with learner._lock:
        d = learner.load_facts()
        for r in found:
            f = next((x for x in d["facts"] if x.get("kind") == "routine" and x.get("key") == r["key"]), None)
            if f is None:
                f = {"id": d["next_id"], "kind": "routine", "key": r["key"], "confirmed": False, "learned": now}
                d["next_id"] += 1
                d["facts"].append(f)
            f.update(value=r["text"], steps=r["steps"], when=r["when"], minute=r["minute"], days=r["days"], last=now)
            out.append(f)
        if out:
            d["facts"] = sorted(d["facts"], key=lambda x: x.get("last", 0))[-learner.MAX_FACTS:]
            learner._save_facts(d)
    return out


def describe(fact):
    """A routine fact as the Knowledge page says it."""
    return f"Your routine: {fact.get('value', '')} (seen on {fact.get('days', '?')} days)."


def status(sampler=None):
    """For the ⚙ page and the indicator: no titles, just the state and counts."""
    s = prefs()
    st = sampler.state if sampler else ("off" if not s["on"] else "paused" if s["paused"] else "recording")
    n = _run("SELECT COUNT(*) FROM spans", fetch=True)[0][0] if store.current().file(DB_FILE).exists() else 0
    return {"on": s["on"], "paused": s["paused"], "state": st, "spans": n, "keep_days": KEEP_DAYS}


def export_json(limit=2000):
    """Everything recorded, for "show me what you logged" (the ⚙ view's download)."""
    return json.dumps(spans(limit=limit), ensure_ascii=False, indent=1)
