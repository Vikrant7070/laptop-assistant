"""Knowing the user from what is already on the laptop (manager plan, Phase 6).

Three sources, each OFF until the user switches it on (Knowledge page, "What Nova knows about me"):
  chrome      - Chrome's History database: which sites you visit, and what you search for (the `?q=` of Google, Bing,
                DuckDuckGo, YouTube...). The file is locked while Chrome runs, so it is copied to a temp folder, read,
                and the copy deleted straight away. Incognito is never in that file.
  powershell  - PSReadLine's ConsoleHost_history.txt: which programs you run (git, python, npm...), names only.
  claude      - Claude Code transcripts in ~/.claude/projects: which projects you work on, and what you told Claude
                about how you like things done ("I prefer...", "always...", "let's go with...").

Everything is summarised here, by plain code - no brain, local or cloud, sees the raw text, and nothing goes over the
network. A secret filter (knowledge.SECRET + tokens, keys, OTPs, card/bank/ID words) drops a line before anything
from it is counted or kept. A "never" list skips sites (github.com) and folders or project names (C:/Work/Secret,
Earn Money); a pause switch stops all reading.

What is kept: counts (tallies) in data/laptop_history.json, and the top few as facts in data/facts.json (kind
"history", with their source), "unconfirmed" until the user ticks them. Deleting a fact keeps it from coming back;
"forget everything from <source>" removes that source's facts and counts (the read position stays, so old history
isn't read again). Reading is incremental (the last point read per file) and runs at idle time, never during a task.
History facts are not put in any prompt yet: they are for the user to see, confirm and delete.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

from nova import store
from nova.log import log_exception

SOURCES = ("chrome", "powershell", "claude")
NAMES = {"chrome": "Chrome history", "powershell": "PowerShell history", "claude": "Claude Code transcripts"}
WHAT = {
    "chrome": "Sites you visit and the words you search for (Google, Bing, YouTube...). Only site names and search "
              "topics are kept, never whole pages. Incognito is never recorded by Chrome.",
    "powershell": "The names of programs you run in PowerShell (git, python, npm...). Never the rest of the command.",
    "claude": "Which projects you work on with Claude Code, and short sentences where you told it how you like things "
              "done (\"I prefer...\", \"always...\").",
}
READ_EVERY = 3600              # seconds between idle-time reads
MAX_ROWS = 50000               # Chrome visits per read
MAX_BYTES = 100_000_000        # transcript bytes per read, newest files first (the rest waits for the next one)
TOP = 8                        # facts per kind
MIN_VISITS, MIN_SEARCHES, MIN_RUNS = 3, 2, 3
MAX_SAID = 10
MAX_TALLY = 500
CHROME_EPOCH = 11644473600     # seconds from 1601-01-01 (Chrome's clock) to 1970-01-01

_lock = threading.RLock()

# Search engines: host part -> the query parameter
SEARCH = {"google.": "q", "bing.com": "q", "duckduckgo.com": "q", "search.brave.com": "q", "ecosia.org": "q",
          "search.yahoo.com": "p", "youtube.com": "search_query", "github.com": "q", "stackoverflow.com": "q",
          "perplexity.ai": "q", "amazon.": "k", "flipkart.com": "q"}
STOP = {"the", "and", "for", "with", "how", "what", "why", "when", "where", "who", "which", "best", "near", "from",
        "into", "that", "this", "are", "was", "can", "does", "you", "your", "not", "use", "using", "get", "make", "free",
        "vs", "www", "com", "http", "https", "new", "top", "between", "about", "way", "ways"}
EXTRA_SECRET = re.compile(
    r"\bsk-[A-Za-z0-9_-]{10,}|\bghp_\w+|github_pat_|\bxox[abp]-|\bAKIA[0-9A-Z]{12,}|\bAIza[0-9A-Za-z_-]{20,}|bearer\s+\S|"
    r"\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9+/_-]{40,}={0,2}|"
    r"\bone[- ]time\b|\bverification\b|\bcvv\b|\biban\b|\bifsc\b|account (?:no|number)|routing number|passport|\bssn\b|"
    r"securestring|credential|\s-p\s*\S|--pass", re.I)
# What counts as the user saying how they like things done, in a Claude Code transcript
SAID = re.compile(r"\b(i prefer|i'd prefer|i'd rather|i like|i don't like|i hate|i want you to|always|never|from now on|"
                  r"let's go with|we decided|i decided|use \S+ instead of)\b", re.I)
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
WORD = re.compile(r"[a-z][a-z0-9+#.-]{2,}")


def _paths_chrome():
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    return sorted(p / "History" for p in base.glob("*") if p.name == "Default" or p.name.startswith("Profile "))


def _paths_powershell():
    return [Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "PowerShell" / "PSReadLine" /
            "ConsoleHost_history.txt"]


def _paths_claude():
    return [Path.home() / ".claude" / "projects"]


# Where each source lives (tests point these at fixture files)
PATHS = {"chrome": _paths_chrome, "powershell": _paths_powershell, "claude": _paths_claude}


# ------------------------------------------------------------------ settings + state (data/laptop_history.json)

def _file():
    return store.current().file("laptop_history.json")


def load():
    with _lock:
        s = store.current().read_json(_file(), {}) or {}
    s.setdefault("version", 1)
    s.setdefault("on", {})
    for src in SOURCES:
        s["on"].setdefault(src, False)
    s.setdefault("never", [])
    s.setdefault("paused", False)
    s.setdefault("cursor", {})
    s.setdefault("tally", {})
    s.setdefault("dropped", [])
    s.setdefault("last_run", 0)
    s.setdefault("last", {})
    return s


def _save(s):
    store.current().write_json(_file(), s)


def secret(text):
    from nova.actions.knowledge import SECRET
    t = str(text or "")
    return bool(SECRET.search(t) or EXTRA_SECRET.search(t))


def set_source(src, on):
    if src not in SOURCES:
        raise ValueError(f"unknown source {src!r}")
    with _lock:
        s = load()
        s["on"][src] = bool(on)
        _save(s)
    return f"{NAMES[src]}: {'on - she reads it when the laptop is quiet' if on else 'off - nothing more is read'}."


def set_paused(paused):
    with _lock:
        s = load()
        s["paused"] = bool(paused)
        _save(s)
    return "Paused: nothing is read until you resume." if paused else "Resumed."


def _norm(x):
    return str(x or "").strip().lower().replace("\\", "/").rstrip("/")


def _is_site(entry):
    return "." in entry and not re.search(r"[\s/:]", entry)


def never_add(entry):
    e = _norm(entry)
    if e.startswith(("http://", "https://")):
        e = host(e)
    if not e:
        return "Give a site (example.com) or a folder / project name."
    with _lock:
        s = load()
        if e not in s["never"]:
            s["never"].append(e)
            _save(s)
    return f"Never read: {e}."


def never_remove(entry):
    e = _norm(entry)
    with _lock:
        s = load()
        s["never"] = [x for x in s["never"] if x != e]
        _save(s)
    return f"Removed {e} from the never list."


def host(url):
    h = (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def never_site(h, never):
    return any(h == n or h.endswith("." + n) for n in never if _is_site(n))


def never_folder(text, never):
    t = _norm(text)
    return any(n in t for n in never if not _is_site(n))


# ------------------------------------------------------------------ reading each source (plain code only)

def _bump(d, key, n=1):
    d[key] = d.get(key, 0) + n


def _cap(d):
    if len(d) > MAX_TALLY:
        for k in sorted(d, key=lambda k: d[k])[:len(d) - MAX_TALLY]:
            del d[k]


def _search_words(url, h):
    for part, param in SEARCH.items():
        if part in h:
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get(param)
            return q[0] if q else None
    return None


def read_chrome(path, cursor, never, tally):
    """New visits in one Chrome History file -> sites and search words counted. Returns (new cursor, read, kept)."""
    tmp = Path(tempfile.mkdtemp(prefix="nova-hist-"))
    read = kept = 0
    try:
        for suffix in ("", "-wal", "-journal"):          # the copy needs Chrome's unwritten pages too
            src = Path(str(path) + suffix)
            if src.exists():
                shutil.copy2(src, tmp / ("History" + suffix))
        con = sqlite3.connect(f"file:{(tmp / 'History').as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT urls.url, visits.visit_time FROM visits JOIN urls ON visits.url = urls.id "
                               "WHERE visits.visit_time > ? ORDER BY visits.visit_time LIMIT ?",
                               (int(cursor or 0), MAX_ROWS)).fetchall()
        finally:
            con.close()
        sites, words = tally.setdefault("sites", {}), tally.setdefault("topics", {})
        for url, vt in rows:
            read += 1
            cursor = max(int(cursor or 0), int(vt or 0))
            url = str(url or "")
            if not url.startswith(("http://", "https://")):
                continue                                  # file://, chrome://, extensions: not sites
            h = host(url)
            if not h or never_site(h, never) or never_folder(url, never):
                continue
            q = _search_words(url, h)
            if q is not None:
                if q.strip() and not secret(q):
                    for w in {w.strip(".-") for w in WORD.findall(q.lower())} - STOP:
                        if len(w) > 2 and not w.isdigit():
                            _bump(words, w)
                    kept += 1
                continue                                  # a results page is a search, not a visit
            _bump(sites, h)
            kept += 1
        _cap(sites)
        _cap(words)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)            # the raw copy never stays
    return cursor, read, kept


def _program(line):
    first = line.strip().split()[0] if line.strip() else ""
    first = first.strip("&.'\"").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if first.endswith((".exe", ".cmd", ".bat", ".ps1")):
        first = first.rsplit(".", 1)[0]
    if not first or first.startswith(("$", "#", "(", "{", "[", "@", "-")) or "=" in first or not re.match(r"^[a-z][\w.-]*$", first):
        return ""
    return first


def read_powershell(path, cursor, never, tally):
    """New lines in ConsoleHost_history.txt -> program names counted. Returns (new cursor, read, kept)."""
    cursor = dict(cursor or {})
    size = path.stat().st_size
    start = cursor.get("lines", 0) if size >= cursor.get("size", 0) else 0     # a smaller file: it was cleared
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    cmds = tally.setdefault("programs", {})
    read = kept = 0
    for line in lines[start:]:
        read += 1
        if not line.strip() or secret(line) or never_folder(line, never):
            continue
        p = _program(line)
        if p:
            _bump(cmds, p)
            kept += 1
    _cap(cmds)
    return {"lines": len(lines), "size": size}, read, kept


def _user_texts(rec):
    if rec.get("type") != "user" or rec.get("isMeta"):
        return []
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    else:
        return []
    return [p for p in parts if p and not p.lstrip().startswith(("<", "Caveat:", "[Request interrupted"))]


def said_lines(text):
    """Sentences where the user says how they like things done (short, plain, nothing secret)."""
    out = []
    for sent in SENTENCE.split(str(text or "")):
        sent = " ".join(sent.split())
        if 15 <= len(sent) <= 180 and SAID.search(sent) and not secret(sent) and "```" not in sent:
            out.append(sent)
    return out


def _project(cwd, file):
    if not cwd:
        return file.parent.name
    path = str(cwd).replace("\\", "/").rstrip("/")
    m = re.match(r"^(.*?)/\.claude/worktrees/", path, re.I)
    if m:                                             # a worktree is the project it belongs to
        path = m.group(1)
    return path.rsplit("/", 1)[-1] or path


def read_claude(root, cursor, never, tally, budget=MAX_BYTES):
    """New lines in every transcript under ~/.claude/projects -> projects and "I prefer..." sentences.
    Returns (new cursor {file: bytes read}, lines read, kept)."""
    cursor = dict(cursor or {})
    projects = tally.setdefault("projects", {})
    said = tally.setdefault("said", [])
    read = kept = 0
    files = sorted(Path(root).glob("*/*.jsonl"), key=lambda p: -p.stat().st_mtime)     # newest first
    for f in files:
        if budget <= 0:
            break
        key, size = str(f), f.stat().st_size
        start = cursor.get(key, 0)
        if start > size:
            start = 0
        if start >= size:
            continue
        with open(f, "rb") as fh:
            fh.seek(start)
            chunk = fh.read(min(size - start, budget))
        end = chunk.rfind(b"\n") + 1 if start + len(chunk) < size else len(chunk)
        if end <= 0:
            continue
        budget -= end
        new_file = start == 0
        seen = False
        for raw in chunk[:end].splitlines():
            read += 1
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            cwd = rec.get("cwd") or ""
            if never_folder(cwd, never) or never_folder(f.parent.name, never):
                continue
            name = _project(cwd, f)
            if never_folder(name, never):
                continue
            ts = rec.get("timestamp") or ""
            texts = _user_texts(rec)
            if texts and not seen:
                p = projects.setdefault(name, {"sessions": 0, "last": ""})
                if new_file:
                    p["sessions"] += 1
                seen = True
            for t in texts:
                if ts and name in projects:
                    projects[name]["last"] = max(projects[name]["last"], ts[:10])
                for s in said_lines(t):
                    if not any(x["text"] == s for x in said):
                        said.append({"text": s, "project": name, "when": ts[:10]})
                        kept += 1
        cursor[key] = start + end
        del said[:-50]
    return cursor, read, kept


# ------------------------------------------------------------------ tallies -> facts (data/facts.json, kind "history")

def _when(day):
    try:
        return time.strftime("%d %b %Y", time.strptime(day, "%Y-%m-%d"))
    except ValueError:
        return day


def wanted(src, tally):
    """The facts this source's counts add up to: {key: text}."""
    out = {}
    if src == "chrome":
        sites, words = tally.get("sites", {}), tally.get("topics", {})
        for h in sorted((h for h in sites if sites[h] >= MIN_VISITS), key=lambda h: -sites[h])[:TOP]:
            out[f"site:{h}"] = f"You often visit {h} ({sites[h]} visits)."
        for w in sorted((w for w in words if words[w] >= MIN_SEARCHES), key=lambda w: -words[w])[:TOP]:
            out[f"topic:{w}"] = f"You often search for things about \"{w}\" ({words[w]} searches)."
    elif src == "powershell":
        cmds = tally.get("programs", {})
        for c in sorted((c for c in cmds if cmds[c] >= MIN_RUNS), key=lambda c: -cmds[c])[:TOP]:
            out[f"program:{c}"] = f"In PowerShell you often run {c} ({cmds[c]} times)."
    elif src == "claude":
        projects = tally.get("projects", {})
        for n in sorted((n for n in projects if projects[n]["sessions"]), key=lambda n: (-projects[n]["sessions"], n))[:TOP]:
            p = projects[n]
            last = f", last {_when(p['last'])}" if p.get("last") else ""
            out[f"project:{n.lower()}"] = (f"You work on \"{n}\" with Claude Code ({p['sessions']} session"
                                           f"{'s' if p['sessions'] != 1 else ''}{last}).")
        for x in tally.get("said", [])[-MAX_SAID:]:
            key = "said:" + hashlib.sha1(x["text"].lower().encode("utf-8")).hexdigest()[:12]
            out[key] = f"You told Claude Code (in \"{x['project']}\"): \"{x['text']}\""
    return out


def _sync(src, s, now):
    """Write this source's wanted facts into facts.json: new ones unconfirmed, changed counts updated, ones that fell
    out of the top removed (unless the user confirmed them), ones the user deleted never re-added."""
    from nova.actions import learner
    want = {k: v for k, v in wanted(src, s["tally"].get(src, {})).items() if f"{src}:{k}" not in s["dropped"]}
    with learner._lock:
        d = learner.load_facts()
        mine = {f.get("key"): f for f in d["facts"] if f.get("kind") == "history" and f.get("source") == src}
        for key, text in want.items():
            f = mine.get(key)
            if f is None:
                f = {"id": d["next_id"], "kind": "history", "source": src, "key": key, "confirmed": False, "learned": now}
                d["next_id"] += 1
                d["facts"].append(f)
            if f.get("text") != text:
                f.update(text=text, last=now)
        gone = {k for k, f in mine.items() if k not in want and not f.get("confirmed")}
        d["facts"] = [f for f in d["facts"] if not (f.get("kind") == "history" and f.get("source") == src and f.get("key") in gone)]
        learner._save_facts(d)
    return len(want)


def facts(src=None):
    from nova.actions import learner
    return [f for f in learner.load_facts()["facts"] if f.get("kind") == "history" and (src is None or f.get("source") == src)]


def delete(fid):
    """Delete one history fact; it won't be made again from the same history."""
    from nova.actions import learner
    f = next((x for x in facts() if x["id"] == fid), None)
    if not f:
        return 0
    with _lock:
        s = load()
        tag = f"{f['source']}:{f['key']}"
        if tag not in s["dropped"]:
            s["dropped"].append(tag)
        _save(s)
    return learner.delete_fact(fid)


def forget(src):
    """Remove every fact and count from one source. The read position stays, so old history isn't read again."""
    from nova.actions import learner
    if src not in SOURCES:
        raise ValueError(f"unknown source {src!r}")
    with _lock:
        s = load()
        s["tally"].pop(src, None)
        s["dropped"] = [t for t in s["dropped"] if not t.startswith(src + ":")]
        s["last"].pop(src, None)
        _save(s)
    with learner._lock:
        d = learner.load_facts()
        before = len(d["facts"])
        d["facts"] = [f for f in d["facts"] if not (f.get("kind") == "history" and f.get("source") == src)]
        learner._save_facts(d)
    return before - len(d["facts"])


# ------------------------------------------------------------------ the idle-time read

READERS = {"chrome": read_chrome, "powershell": read_powershell, "claude": read_claude}


def due(now=None):
    s = load()
    return not s["paused"] and any(s["on"].values()) and (now or time.time()) - s["last_run"] >= READ_EVERY


def run(busy=None, now=None):
    """Read every switched-on source from where it stopped last time, then update the facts. `busy()` -> True stops
    between sources (a task started). Nothing is read while paused or for a source that is off. -> {src: summary}."""
    now = now or time.time()
    out = {}
    with _lock:
        s = load()
        if s["paused"]:
            return out
        s["last_run"] = now
        _save(s)
    for src in SOURCES:
        if not s["on"].get(src):
            continue
        if busy and busy():
            break
        read = kept = 0
        try:
            for p in PATHS[src]():
                if not p.exists():
                    continue
                key = str(p)
                cur, r, k = READERS[src](p, s["cursor"].setdefault(src, {}).get(key), s["never"], s["tally"].setdefault(src, {}))
                s["cursor"][src][key] = cur
                read, kept = read + r, kept + k
        except Exception:
            log_exception(f"laptop history: reading {src}")
            out[src] = {"error": True}
            continue
        with _lock:
            fresh = load()                                # settings may have changed while reading
            if not fresh["on"].get(src) or fresh["paused"]:
                continue
            fresh["cursor"][src] = s["cursor"][src]
            if src in s["tally"]:
                fresh["tally"][src] = s["tally"][src]
            n = _sync(src, fresh, now)
            fresh["last"][src] = {"when": now, "read": read, "kept": kept, "facts": n}
            _save(fresh)
            s = fresh
        out[src] = {"read": read, "kept": kept, "facts": n}
    return out


def status():
    """For the Knowledge page: each source with its switch, what it reads, where, the last read and its facts."""
    s = load()
    rows = []
    for src in SOURCES:
        paths = PATHS[src]()
        last = s["last"].get(src) or {}
        rows.append({"id": src, "name": NAMES[src], "what": WHAT[src], "on": s["on"][src],
                     "found": any(p.exists() for p in paths), "where": [str(p) for p in paths][:3],
                     "last": time.strftime("%d %b %Y %H:%M", time.localtime(last["when"])) if last.get("when") else "",
                     "read": last.get("read", 0), "kept": last.get("kept", 0),
                     "facts": [{"id": f["id"], "text": f.get("text", ""), "confirmed": f.get("confirmed", False),
                                "when": time.strftime("%d %b %Y", time.localtime(f.get("last", f.get("learned", 0))))}
                               for f in sorted(facts(src), key=lambda f: f["id"])]})
    return {"sources": rows, "never": s["never"], "paused": s["paused"], "file": str(_file())}
