"""Skills: new abilities for Nova written as small text files instead of Python.

Each skill is a Markdown file in skills/ (NOVA_SKILLS points elsewhere; the tests use a temp folder).

Format 2 (the one to write; Nova's own saves use it):

    ---
    name: anime-time
    format: 2
    version: 3
    description: Sets up Crunchyroll for watching - dark mode, open the site, sign in if needed, fullscreen.
    examples: anime time, set up crunchyroll, crunchyroll fullscreen
    not_for: check the latest anime, which anime should i watch
    requires_any: anime time, crunchyroll
    risk: disruptive              (read_only | disruptive | consequential; never lower than the steps need)
    invoke: confirm_if_fuzzy      (auto | confirm_if_fuzzy | explicit_only)
    status: active                (draft | active | deprecated)
    ---
    s1. set_dark_mode enabled=true
    s2. open_website url_or_search=https://www.crunchyroll.com
        expect: window_open "Crunchyroll"
        on_fail: retry 1, then stop "Crunchyroll didn't open"
    s3. if page_has "Log In" goto s4
    s4. login_if_needed window=Crunchyroll
    s5. say Crunchyroll is ready, boss!

  Steps: a tool call (`tool key=value, key=value` or `tool {"json": "args"}`), `say <text>`, `wait <secs>`,
  `if <check> goto sN` (forward only), `confirm "<question>"` (pauses and asks Allow / No), `brain "<one
  instruction>" [tools: a, b] [slot: name]` (one small model call for that step only) and `report "<instruction>"`
  (last step: the brain words the answer from the results). Indented under a step: `expect: <check>` (checked by
  code, see checks.py), `on_fail: retry N | stop "msg" | ask "q" | brain "..." | skip | goto sN` (chain them with
  commas), `note: <text>`. `{input}` / `{input_url}` are replaced by what the user asked for.

Format 1 (older files, still run): the same without ids and checks, `when:` instead of `examples:`, and a skill
whose body is plain English is a "recipe" the brain follows. Recipes are the weak spot of a small model - it skips
steps and claims success - so new skills are routines, and code runs them.

Skills only combine Nova's existing tools, so every step still goes through the same approval rules as a direct
call (app.is_risky) - a skill can never get around the "ask first" step. Side files, written only by code, never
by the model: skills/.stats/<name>.json (runs, failures by step, ...), skills/.history/ (every old version, so a
change can be undone), skills/.trash/ (deleted skills).
"""
import json
import re
import shutil
import time
from nova import store
from nova.core import characters

from nova.brain import retrieval

MAX_STEPS = 25
MAX_BODY = 2500
MAX_ACTIVE = 40
STEP_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s*(.+?)\s*$")
NAME_RE = re.compile(r"[^a-z0-9]+")
KV_RE = re.compile(r'(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|\'[^\']*\'|[^,]*)')
QUOTED = r'"((?:[^"\\]|\\.)*)"'
ID_RE = re.compile(r"^(s\d+)\s*[.):]\s*(.*)$", re.I)
ATTR_RE = re.compile(r"^(expect|on_fail|note|slot)\s*:\s*(.*)$", re.I)
LOCKED = ("run_skill", "save_skill", "delete_skill", "more_tools", "edit_skill", "undo_skill_change",
          "save_task_as_skill", "start_watching", "stop_watching")   # no skills inside skills, and a skill can't rewrite skills

RISKS = ("read_only", "disruptive", "consequential")
INVOKES = ("auto", "confirm_if_fuzzy", "explicit_only")
STATUSES = ("draft", "active", "deprecated")
CONSEQUENTIAL = {"kill_process", "organize_folder", "delete_to_recycle_bin", "clean_temp_files", "run_powershell",
                 "run_in_terminal", "write_text_file", "move_or_rename", "install_app", "uninstall_app", "power_action",
                 "close_all_windows", "empty_recycle_bin", "toggle_wifi", "set_wallpaper", "draft_email", "fill_form",
                 "start_coding_job", "commit_coding_job", "retry_coding_job"}
READ_ONLY = ("get_", "list_", "read_", "look_", "find_", "search_", "check_", "show_", "recall_", "network_info",
             "temp_files_size", "background_jobs", "research_in_background", "research_report",
             "take_screenshot")

# Fuzzy matching must clear BOTH bars. (Tuned on the real log with `python -m nova.tools.skill_eval`.)
FUZZY_FLOOR = 0.85            # with the embedding model (measured with nova.skill_eval: 0.75 let "good night" start the morning briefing); FUZZY_FLOOR_WORDS is for the word-overlap fallback
FUZZY_FLOOR_WORDS = 0.6
FUZZY_MARGIN = 0.05           # ... and beat the next-best skill by this much
# Every character's name ("hey lucy, movie time" is the same request as "hey nova, movie time").
_NAMES = tuple(n for n in characters.all_names() if " " not in n) or ("nova",)
FILLERS = (*(f"{g} {n}" for n in _NAMES for g in ("hey", "hi", "ok", "okay", "hello")), *_NAMES,
           "hey", "hi", "ok", "okay", "please", "can you", "could you", "would you", "will you", "so", "just", "now", "and")
TRAILING = ("please", *_NAMES, "now", "for me")
STOPWORDS = set("""a an the and or of to is are was were be been i me my you your it its this that these those can could
would will do does did what which who whom how where when why for on in at with about tell check please hey ok
okay so just some any want need let s from as if then than too very up out one""".split()) | set(_NAMES)

last_decision = ""            # why the last pick() answered as it did - app.new_task writes it to the task log


def slug(name):
    return NAME_RE.sub("-", str(name or "").lower()).strip("-")[:40]


# ------------------------------------------------------------------ parsing

def _tool_names():
    from nova.actions import tools
    return {t["name"] for t in tools.TOOLS}


def _value(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].replace('\\"', '"')
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v) if re.fullmatch(r"-?\d+", v) else float(v) if re.fullmatch(r"-?\d+\.\d+", v) else v
    except ValueError:
        return v


def parse_step(line, known=None):
    """One routine line -> {"tool"/"say"/"wait": ..., "args": {...}}, or None if it isn't a step."""
    line = line.strip().rstrip(".") if not line.strip().lower().startswith("say") else line.strip()
    m = re.match(r"^(say|wait)\b[:\s]*(.*)$", line, re.I)
    if m:
        what, rest = m.group(1).lower(), m.group(2).strip()
        if what == "say":
            return {"say": rest} if rest else None
        try:
            return {"wait": min(30.0, max(0.0, float(rest.split()[0] if rest else 1)))}
        except ValueError:
            return None
    m = re.match(r"^(\w+)\s*(.*)$", line)
    if not m:
        return None
    name, rest = m.group(1), m.group(2).strip()
    if name not in (known or _tool_names()) or name in LOCKED:
        return None
    if not rest:
        return {"tool": name, "args": {}}
    if rest.startswith("{"):
        try:
            args = json.loads(rest)
            return {"tool": name, "args": args} if isinstance(args, dict) else None
        except ValueError:
            return None
    pairs = KV_RE.findall(rest)
    if not pairs or KV_RE.sub("", rest).replace(",", "").strip():
        return None                     # leftover words: this is a sentence, not a step
    return {"tool": name, "args": {k: _value(v) for k, v in pairs}}


def _steps(body):
    return [STEP_RE.match(l).group(1) for l in body.splitlines() if STEP_RE.match(l)]


def parse_routine(body):
    """The steps of a (format 1) routine body, or (None, the first line that isn't a step)."""
    known = _tool_names()
    lines = _steps(body) or [l.strip() for l in body.splitlines() if l.strip()]
    out = []
    for l in lines:
        s = parse_step(l, known)
        if s is None:
            return None, l
        out.append(s)
    return out[:MAX_STEPS], None


# ---- format 2 steps

def _unq(s):
    return s.replace('\\"', '"').replace("\\\\", "\\")


def parse_check(text):
    """`window_open "Crunchyroll"` -> {"check": "window_open", "arg": "Crunchyroll"}, or None."""
    from nova.actions import checks
    m = re.match(r"^(\w+)\s*(.*)$", str(text or "").strip())
    if not m or m.group(1) not in checks.NAMES:
        return None
    arg = m.group(2).strip()
    if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'":
        arg = _unq(arg[1:-1])
    if m.group(1) in checks.NEEDS_ARG and not arg:
        return None
    return {"check": m.group(1), "arg": arg}


ACTION_RE = re.compile(r'\s*(?:[,;]\s*)?(?:then\s+)?(retry\s+\d+|stop(?:\s+' + QUOTED + r')?|ask\s+' + QUOTED + r'|brain\s+'
                       + QUOTED + r'|skip\b|goto\s+s\d+)', re.I)


def parse_on_fail(text):
    """`retry 1, then stop "msg"` -> [{"do": "retry", "n": 1}, {"do": "stop", "msg": "msg"}], or None if it's not one."""
    text, pos, out = str(text or "").strip(), 0, []
    while pos < len(text):
        m = ACTION_RE.match(text, pos)
        if not m:
            return None if text[pos:].strip(" ,;") else out
        word = m.group(1).lower()
        quoted = re.search(QUOTED, m.group(1))
        arg = _unq(quoted.group(1)) if quoted else ""
        if word.startswith("retry"):
            out.append({"do": "retry", "n": min(3, int(word.split()[1]))})
        elif word.startswith("stop"):
            out.append({"do": "stop", "msg": arg})
        elif word.startswith("ask"):
            out.append({"do": "ask", "q": arg})
        elif word.startswith("brain"):
            out.append({"do": "brain", "instr": arg})
        elif word.startswith("skip"):
            out.append({"do": "skip"})
        else:
            out.append({"do": "goto", "id": word.split()[1]})
        pos = m.end()
    return out


def parse_step_v2(text, known=None):
    """One format-2 step (without its id) -> a step dict, or None."""
    text = text.strip()
    m = re.match(r"^if\s+(.+?)\s+goto\s+(s\d+)\s*$", text, re.I)
    if m:
        chk = parse_check(m.group(1))
        return {"if": chk, "goto": m.group(2).lower()} if chk else None
    m = re.match(r"^(confirm|report)\s+" + QUOTED + r"\s*$", text, re.I)
    if m:
        return {m.group(1).lower(): _unq(m.group(2))}
    m = re.match(r"^brain\s+" + QUOTED + r"\s*(.*)$", text, re.I)
    if m:
        step, rest = {"brain": _unq(m.group(1)), "tools": [], "slot": ""}, m.group(2).strip()
        tm = re.search(r"\btools:\s*([\w,\s]+?)(?=\s+slot:|$)", rest)
        sm = re.search(r"\bslot:\s*(\w+)", rest)
        if tm:
            step["tools"] = [t.strip() for t in tm.group(1).split(",") if t.strip()]
        if sm:
            step["slot"] = sm.group(1)
        leftover = re.sub(r"\btools:\s*([\w,\s]+?)(?=\s+slot:|$)", "", rest)
        leftover = re.sub(r"\bslot:\s*\w+", "", leftover).strip()
        return None if leftover else step
    return parse_step(text, known)


def parse_body_v2(body):
    """Format-2 body -> (steps, None) or (None, "the line that is wrong")."""
    known, steps = _tool_names(), []
    used = set()                                          # ids written out in the file: auto ids skip them
    for l in body.splitlines():
        m = ID_RE.match(re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", l.strip()))
        if m:
            used.add(m.group(1).lower())
    counter = 0
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        am = ATTR_RE.match(line)
        if am and steps:
            key, val = am.group(1).lower(), am.group(2).strip()
            st = steps[-1]
            if key == "expect":
                chk = parse_check(val)
                if not chk:
                    return None, line
                st["expect"] = chk
            elif key == "on_fail":
                acts = parse_on_fail(val)
                if not acts:
                    return None, line
                st["on_fail"] = acts
            elif key == "note":
                st["note"] = val
            elif key == "slot":
                st["slot"] = val
            continue
        line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", line)
        im = ID_RE.match(line)
        sid = im.group(1).lower() if im else ""
        text = im.group(2) if im else line
        st = parse_step_v2(text, known)
        if st is None:
            return None, raw.strip()
        if not sid:
            counter += 1
            while f"s{counter}" in used:
                counter += 1
            sid = f"s{counter}"
        elif sid in {s["id"] for s in steps}:
            return None, raw.strip()
        used.add(sid)
        slot = st.pop("slot", "")
        st.update(id=sid, expect=None, on_fail=None, note="")
        if "brain" in st:
            st["slot"] = slot
            st.setdefault("tools", [])
        steps.append(st)
    if not steps:
        return None, ""
    ids = [s["id"] for s in steps]
    for i, st in enumerate(steps):
        targets = ([st["goto"]] if "goto" in st else []) + [a["id"] for a in (st["on_fail"] or []) if a["do"] == "goto"]
        for t in targets:
            if t not in ids or ids.index(t) <= i:
                return None, f"{st['id']}: goto {t} must jump forward to a step that exists"
        if "report" in st and i != len(steps) - 1:
            return None, f"{st['id']}: report must be the last step"
        for tname in st.get("tools", []):
            if tname not in known or tname in LOCKED:
                return None, f"{st['id']}: {tname} is not a tool I can use here"
    if len(steps) > MAX_STEPS:
        return None, f"more than {MAX_STEPS} steps"
    return steps, None


def _front(text):
    meta, body = {}, text
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip()
        body = m.group(2)
    return meta, body.strip()


def _list(v):
    return [x.strip().strip("\"'") for x in str(v or "").strip("[]").split(",") if x.strip().strip("\"'")]


def tool_names_of(steps):
    out = []
    for s in steps:
        out += ([s["tool"]] if "tool" in s else []) + list(s.get("tools", []))
    return list(dict.fromkeys(out))


def compute_risk(tools_):
    """What the steps can do: change nothing (read_only), change the screen or settings (disruptive), or do something
    that is hard to undo or leaves the laptop (consequential)."""
    if any(t in CONSEQUENTIAL for t in tools_):
        return "consequential"
    if all(t.startswith(READ_ONLY) for t in tools_):
        return "read_only"
    return "disruptive"


def parse(text, fallback_name=""):
    meta, body = _front(text)
    name = slug(meta.get("name") or fallback_name)
    if not name or not body:
        return None
    fmt = 2 if str(meta.get("format", "")).strip() == "2" else 1
    kind = meta.get("kind", "").lower()
    err = ""
    if fmt == 2:
        steps, bad = parse_body_v2(body)
        if steps is None:
            kind, steps, err = "broken", [], f"this line is not a step: {bad}" if bad else "no steps"
        else:
            kind = "routine"
        tools_ = tool_names_of(steps)
    else:
        steps, bad = parse_routine(body) if kind != "recipe" else (None, None)
        if kind != "routine" and steps is None:
            kind = "recipe"
        elif steps is not None:
            kind = "routine"
        else:
            kind = "broken"
        err = f"this line is not a step: {bad}" if kind == "broken" else ""
        tools_ = _list(meta.get("tools"))
        if kind == "routine":
            tools_ = list(dict.fromkeys(s["tool"] for s in steps if "tool" in s))
        elif kind == "recipe":
            known = _tool_names()
            tools_ = [t for t in dict.fromkeys(tools_ + re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", body)) if t in known]
    examples = list(dict.fromkeys(_list(meta.get("examples")) + _list(meta.get("when"))))
    explicit = meta.get("risk", "").lower()
    computed = compute_risk(tools_)
    risk = max([computed] + ([explicit] if explicit in RISKS else []), key=RISKS.index)
    status = meta.get("status", "active").lower()
    status = status if status in STATUSES else "active"
    invoke = meta.get("invoke", "").lower()
    invoke = invoke if invoke in INVOKES else "confirm_if_fuzzy"
    if status == "draft":
        invoke = "explicit_only"
    try:
        version = max(1, int(meta.get("version", 1)))
    except ValueError:
        version = 1
    return {"name": name, "description": meta.get("description", ""), "when": examples, "examples": examples,
            "not_for": _list(meta.get("not_for")), "requires_any": _list(meta.get("requires_any")),
            "risk": risk, "invoke": invoke, "status": status, "format": fmt, "version": version,
            "kind": kind, "body": body[:MAX_BODY], "steps": steps or [], "tools": tools_, "error": err,
            "needs_input": "{input" in body}


# ------------------------------------------------------------------ the folder

_cache = {"key": None, "skills": []}


def load():
    """All skills, re-read only when a file in the folder changed."""
    try:
        files = sorted(store.current().skills_dir.glob("*.md"))
        # mtime alone isn't enough: Windows file times are coarse, so a save right after another kept the same key
        # and the old skill was read back (seen as a flaky test). The size usually changes; _write also clears it.
        key = (str(store.current().skills_dir),) + tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)
    except OSError:
        return []
    if key != _cache["key"]:
        out = []
        for f in files:
            try:
                s = parse(f.read_text(encoding="utf-8"), f.stem)
            except (OSError, UnicodeDecodeError):
                continue
            if s:
                s["file"] = str(f)
                out.append(s)
        _cache.update(key=key, skills=out)
    return _cache["skills"]


def get(name):
    n = slug(name)
    return next((s for s in load() if s["name"] == n), None)


# ------------------------------------------------------------------ matching

def _norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower())).strip()


def _strip_fillers(q):
    """'hey nova, can you movie time please' -> 'movie time'."""
    changed = True
    while changed and q:
        changed = False
        for f in FILLERS:
            if q == f:
                return ""
            if q.startswith(f + " "):
                q, changed = q[len(f) + 1:], True
        for f in TRAILING:
            if q.endswith(" " + f):
                q, changed = q[:-len(f) - 1], True
    return q


def _content(text):
    return {w for w in _norm(text).split() if w not in STOPWORDS and len(w) > 1}


def _has(q, phrase):
    return f" {phrase} " in f" {q} "


def _phrase_fires(q, p, needs_input):
    """Does the request start this skill outright? One word: only the whole request (or, for a skill that takes
    input, the first word: 'research quantum batteries'). Two words: at the start or the end ('it's movie time'). Three
    or more: anywhere. A loose word in the middle of a sentence ('where did you find out') never starts a skill."""
    n = len(p.split())
    if not n:
        return False
    if q == p:
        return True
    if n == 1:
        return needs_input and q.startswith(p + " ")
    if n == 2:
        return q.startswith(p + " ") or q.endswith(" " + p)
    return _has(q, p)


def _vetoed(q, s):
    words = set(q.split())
    for phrase in s["not_for"] + stats_get(s["name"]).get("learned_not_for", []):
        p = _norm(phrase)
        if not p:
            continue
        if _has(q, p):
            return phrase
        cw = _content(p)
        if len(cw) >= 2 and cw <= words:
            return phrase
    return ""


def _requires_ok(q, s):
    return not s["requires_any"] or any(_has(q, _norm(r)) for r in s["requires_any"] if _norm(r))


def floor():
    return FUZZY_FLOOR if retrieval.ready() else FUZZY_FLOOR_WORDS


def pick(query):
    """The skill this request is about -> (skill, "exact" | "fuzzy"), or (None, None). Exact: the request says the
    skill's name or one of its example phrases. Fuzzy: it only sounds like it - Nova must ask before a fuzzy match
    that changes things (app.is_risky). A request a skill's `not_for` phrase describes never starts it, and a
    request that matches nothing well gets no skill at all (so it is just answered)."""
    global last_decision
    q = _strip_fillers(_norm(query))
    skills = [s for s in load() if s["kind"] != "broken" and s["status"] != "deprecated"]
    if not skills or not q:
        last_decision = "SKILL NO MATCH (nothing to match)"
        return None, None
    vetoes, live = [], []
    for s in skills:
        v = _vetoed(q, s)
        if v:
            vetoes.append(f"{s['name']} vetoed by '{v}'")
        else:
            live.append(s)
    exact = []
    for s in live:
        for p in [_norm(s["name"])] + [_norm(e) for e in s["examples"]]:
            if p and _phrase_fires(q, p, s["needs_input"]) and _requires_ok(q, s):
                exact.append((len(p.split()), len(p), s, p))
                break
    if exact:
        _, _, s, p = max(exact, key=lambda e: e[:2])
        last_decision = f"SKILL MATCH exact {s['name']} (phrase '{p}')"
        return s, "exact"
    # A guess needs something to go on: two meaningful words at least, and a skill that has to be told what to work on
    # ("research <topic>") can't start from a guess at all - it has no way to know the topic.
    cands = [s for s in live if s["invoke"] != "explicit_only" and s["status"] == "active" and not s["needs_input"]
             and _requires_ok(q, s)] if len(_content(q)) >= 2 else []
    why = "; ".join(vetoes)
    if not cands:
        last_decision = "SKILL NO MATCH" + (f" ({why})" if why else " (no exact phrase)")
        return None, None
    texts, owner = [], []
    for s in cands:
        for t in [f"{s['name'].replace('-', ' ')}: {s['description']}"] + s["examples"]:
            texts.append(t)
            owner.append(s)
    sc = retrieval.scores(q, texts)
    best = {}
    for s, x in zip(owner, sc):
        best[s["name"]] = max(best.get(s["name"], 0.0), x)
    ranked = sorted(cands, key=lambda s: best[s["name"]], reverse=True)
    top, top_score = ranked[0], best[ranked[0]["name"]]
    second = best[ranked[1]["name"]] if len(ranked) > 1 else 0.0
    own = _content(" ".join([top["name"].replace("-", " ")] + top["examples"]))
    overlap = bool(own & _content(q))
    bar = floor() + (0 if overlap else 0.1)
    detail = f"{top['name']} score={top_score:.2f} 2nd={second:.2f} floor={bar:.2f} overlap={overlap}"
    if top_score < bar:
        last_decision = f"SKILL NO MATCH below the floor ({detail})" + (f" [{why}]" if why else "")
    elif top_score - second < FUZZY_MARGIN:
        last_decision = f"SKILL NO MATCH too close to call ({detail})"
    elif not overlap and top["risk"] == "read_only":
        last_decision = f"SKILL NO MATCH no shared words ({detail})"
    else:
        last_decision = f"SKILL MATCH fuzzy {detail}"
        return top, "fuzzy"
    return None, None


def match(query):
    """The skill this request is about, or None (see pick)."""
    return pick(query)[0]


def guess_input(skill, query):
    """For a routine with {input}: the part of the request after the trigger phrase ("play lofi music" -> "lofi music")."""
    low = query.lower()
    for p in sorted([skill["name"].replace("-", " ")] + skill["examples"], key=len, reverse=True):
        i = low.find(p.lower())
        if p and i >= 0:
            rest = query[i + len(p):].strip(" ,.:-")
            rest = re.sub(r"^(for|about|of|to|on)\s+", "", rest, flags=re.I)
            if rest:
                return rest
    return ""


# The request asks for more than the skill ("research tea and then email it"): the brain has to plan the rest.
MORE_WORK = re.compile(r"\b(and then|then|after that|afterwards|also|as well|plus)\b|\band (?:also )?(?:send|email|mail|open|"
                       r"save|add|tell|show|make|write|put|play|share|text|message|post|commit)\b", re.I)


def direct_args(skill, query):
    """run_skill's arguments when an exact match can run without asking the brain first, else None.
    Only an active routine (its steps run as code anyway), only when the request is just the skill, and only when
    a needed {input} can be read from the request. (Live 2026-11-21: on an exact 'web-research' match the small brain
    called list_windows and focused the user's Netflix window before - or instead of - running the skill.)"""
    if not skill or skill.get("kind") != "routine" or skill.get("status") != "active" or MORE_WORK.search(query or ""):
        return None
    args = {"name": skill["name"]}
    if skill.get("needs_input"):
        guess = guess_input(skill, query)
        if not guess:
            return None
        args["input"] = guess
    return args


def prompt_block(query, matched=None, how="exact"):
    """What goes into the prompt about skills for this request: the matching one in full, and the names of the rest."""
    skills = [s for s in load() if s["kind"] != "broken" and s["status"] == "active"]
    if not skills:
        return ""
    lines = []
    if matched and how == "fuzzy":
        lines.append(f"The user MIGHT want your skill '{matched['name']}' ({matched['description'] or 'a routine'}). "
                     f"Call run_skill(name='{matched['name']}') only if the request really asks for that. If it is a "
                     "question or something else, ignore the skill and just help.")
    elif matched:
        if matched["kind"] == "routine":
            arg = ""
            if matched["needs_input"]:
                guess = guess_input(matched, query)
                arg = f", input='{guess}'" if guess else ", input=<what the user wants it for>"
            lines.append(f"Your skill '{matched['name']}' fits this request ({matched['description'] or 'a routine'}). "
                         f"Call run_skill(name='{matched['name']}'{arg}) - it does all the steps itself; don't do "
                         "them one by one.")
        else:
            lines.append(f"Your skill '{matched['name']}' fits this request. Follow it, one tool call at a time:\n"
                         + matched["body"])
    others = [s["name"] for s in skills if s is not matched][:20]
    if others:
        lines.append("Your other saved skills (run one with run_skill): " + ", ".join(others) + ".")
    return "\n\n" + "\n\n".join(lines)


# ------------------------------------------------------------------ side files: stats and history

def _side(kind):
    return store.current().skills_dir / f".{kind}"


def stats_get(name):
    try:
        d = json.loads((_side("stats") / f"{slug(name)}.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def stats_save(name, d):
    try:
        _side("stats").mkdir(parents=True, exist_ok=True)
        (_side("stats") / f"{slug(name)}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def stats_update(name, fn):
    d = stats_get(name)
    fn(d)
    stats_save(name, d)
    return d


def add_not_for(name, text):
    """The user said no to a fuzzy match: remember that sentence so it never starts this skill again."""
    t = _norm(text)
    if not t:
        return

    def add(d):
        d["denied"] = d.get("denied", 0) + 1
        lst = [x for x in d.get("learned_not_for", []) if x != t] + [t]
        d["learned_not_for"] = lst[-50:]
    stats_update(name, add)


def record_run(name, ok, failed_step="", error="", stopped=False, author="code"):
    """Count one run of a skill. Returns a note for the user when this run promoted a draft or retired a failing
    skill (else "")."""
    def add(d):
        d["runs"] = d.get("runs", 0) + 1
        d["last_used"] = int(time.time())
        recent = (d.get("recent", []) + [1 if ok else 0])[-10:]
        d["recent"] = recent
        if ok:
            d["ok"] = d.get("ok", 0) + 1
            d["last_ok"] = d["last_used"]
        elif stopped:
            d["stopped_by_user"] = d.get("stopped_by_user", 0) + 1
        else:
            fb = d.setdefault("failed_by_step", {})
            fb[failed_step or "?"] = fb.get(failed_step or "?", 0) + 1
            d["last_error"] = str(error)[:200]
    d = stats_update(name, add)
    s = get(name)
    if not s or stopped:
        return ""
    recent = d.get("recent", [])
    if s["status"] == "draft" and ok and d.get("ok", 0) >= 2 and recent[-2:] == [1, 1]:
        set_status(name, "active", cause="2 verified runs", author=author)
        return f"My '{name}' skill worked twice in a row, so I'm keeping it - you can now say it any time."
    if s["status"] == "active" and d.get("runs", 0) >= 5 and len(recent) >= 5 and sum(recent[-5:]) / 5 < 0.4:
        step = max(d.get("failed_by_step", {"?": 1}).items(), key=lambda kv: kv[1])[0]
        set_status(name, "deprecated", cause="failed 3+ of the last 5 runs", author=author)
        return (f"My '{name}' skill keeps failing (mostly at step {step}), so I've stopped using it. "
                "Want to show me how it should work?")
    return ""


def _history_add(name, entry):
    try:
        _side("history").mkdir(parents=True, exist_ok=True)
        with (_side("history") / f"{name}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def history(name):
    try:
        return [json.loads(l) for l in (_side("history") / f"{slug(name)}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def _set_front(text, key, value):
    m = re.match(r"^(﻿?---\s*\n)(.*?)(\n---\s*\n?)(.*)$", text, re.S)
    if not m:
        return text
    lines, done = m.group(2).splitlines(), False
    for i, l in enumerate(lines):
        if l.split(":", 1)[0].strip().lower() == key:
            lines[i], done = f"{key}: {value}", True
    if not done:
        lines.append(f"{key}: {value}")
    return m.group(1) + "\n".join(lines) + m.group(3) + m.group(4)


def _write(name, text, author="local", cause="", op=""):
    """Write a skill file. The file it replaces is kept in .history (so the change can be undone) and the change is
    logged. Returns the new version number."""
    store.current().skills_dir.mkdir(parents=True, exist_ok=True)
    f = store.current().skills_dir / f"{name}.md"
    old_ver, backup = 0, ""
    if f.exists():
        old = f.read_text(encoding="utf-8")
        try:
            old_ver = int(_front(old)[0].get("version", 1))
        except ValueError:
            old_ver = 1
        _side("history").mkdir(parents=True, exist_ok=True)
        backup = f"{name}.v{old_ver}.md"
        if (_side("history") / backup).exists():
            backup = f"{name}.v{old_ver}-{int(time.time())}.md"
        (_side("history") / backup).write_text(old, encoding="utf-8")
    ver = old_ver + 1
    f.write_text(_set_front(text, "version", ver), encoding="utf-8")
    _cache["key"] = None                       # read it back fresh, whatever the file time says
    _history_add(name, {"ts": int(time.time()), "version": ver, "parent": old_ver or None, "author": author,
                        "cause": str(cause)[:200], "op": op, "backup": backup})
    return ver


def set_status(name, status, cause="", author="code"):
    f = store.current().skills_dir / f"{slug(name)}.md"
    if status not in STATUSES or not f.exists():
        return False
    text = _set_front(f.read_text(encoding="utf-8"), "status", status)
    _write(slug(name), text, author=author, cause=cause, op=f"set_status {status}")
    return True


def undo(name):
    """Put back the version a change replaced (undoing it again redoes it)."""
    n = slug(name)
    last = next((h for h in reversed(history(n)) if h.get("backup")), None)
    f = store.current().skills_dir / f"{n}.md"
    if not last or not (_side("history") / last["backup"]).exists() or not f.exists():
        return f"There is no earlier version of '{n}' to go back to."
    old = (_side("history") / last["backup"]).read_text(encoding="utf-8")
    _write(n, old, author="user", cause="undo", op="undo")
    return f"Put back the earlier version of '{n}'."


# ------------------------------------------------------------------ writing skills

def _fmt_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or re.search(r"[\s,\"'=]", s) or s.startswith("{"):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _q(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_check(chk):
    return chk["check"] + (f" {_q(chk['arg'])}" if chk.get("arg") else "")


def render_on_fail(acts):
    parts = []
    for a in acts:
        d = a["do"]
        parts.append({"retry": lambda: f"retry {a.get('n', 1)}", "stop": lambda: "stop" + (f" {_q(a['msg'])}" if a.get("msg") else ""),
                      "ask": lambda: f"ask {_q(a.get('q', ''))}", "brain": lambda: f"brain {_q(a.get('instr', ''))}",
                      "skip": lambda: "skip", "goto": lambda: f"goto {a.get('id')}"}[d]())
    return ", then ".join(parts)


def render_step(st):
    """A step dict -> its one line of text (without the id), the opposite of parse_step_v2."""
    if "say" in st:
        return f"say {st['say']}"
    if "wait" in st:
        return f"wait {st['wait']:g}"
    if "if" in st:
        return f"if {render_check(st['if'])} goto {st['goto']}"
    if "confirm" in st:
        return f"confirm {_q(st['confirm'])}"
    if "report" in st:
        return f"report {_q(st['report'])}"
    if "brain" in st:
        extra = (f" tools: {', '.join(st['tools'])}" if st.get("tools") else "") + (f" slot: {st['slot']}" if st.get("slot") else "")
        return f"brain {_q(st['brain'])}{extra}"
    args = st.get("args", {})
    if any(isinstance(v, (dict, list)) for v in args.values()):
        return f"{st['tool']} {json.dumps(args, ensure_ascii=False)}"
    return st["tool"] + (" " + ", ".join(f"{k}={_fmt_value(v)}" for k, v in args.items()) if args else "")


def render_body(steps):
    lines = []
    for st in steps:
        lines.append(f"{st['id']}. {render_step(st)}")
        if st.get("expect"):
            lines.append(f"    expect: {render_check(st['expect'])}")
        if st.get("on_fail"):
            lines.append(f"    on_fail: {render_on_fail(st['on_fail'])}")
        if st.get("note"):
            lines.append(f"    note: {st['note']}")
    return "\n".join(lines)


def _phrases(v):
    return ", ".join(dict.fromkeys(re.sub(r"\s+", " ", str(x).replace(",", " ")).strip() for x in v if str(x).strip()))


def render_v2(s, steps):
    """The text of a format-2 skill. `s` has name, description, examples, not_for, requires_any, risk (only written
    when explicit), invoke, status."""
    head = [f"name: {s['name']}", "format: 2", "version: 1", f"description: {s.get('description', '')}"]
    for key in ("examples", "not_for", "requires_any"):
        if s.get(key):
            head.append(f"{key}: {_phrases(s[key])}")
    if s.get("risk_explicit"):
        head.append(f"risk: {s['risk_explicit']}")
    head += [f"invoke: {s.get('invoke', 'confirm_if_fuzzy')}", f"status: {s.get('status', 'active')}"]
    return "---\n" + "\n".join(head) + "\n---\n" + render_body(steps) + "\n"


def _render(name, description, when, kind, body):
    """A format-1 file (recipes and old-style routines)."""
    return (f"---\nname: {name}\ndescription: {description}\n" + (f"when: {', '.join(when)}\n" if when else "")
            + f"kind: {kind}\n---\n{body.strip()}\n")


def _explicit_risk(name):
    f = store.current().skills_dir / f"{name}.md"
    try:
        r = _front(f.read_text(encoding="utf-8"))[0].get("risk", "").lower()
        return r if r in RISKS else ""
    except OSError:
        return ""


def _as_v2_text(s, steps):
    return render_v2({**s, "risk_explicit": _explicit_risk(s["name"])}, steps)


def save(name, description, steps, when="", kind="", replace=False, author="local", cause="", not_for="",
         requires_any="", status="active"):
    """Write a new skill. Returns a message for the brain. A skill that already exists is not overwritten unless
    replace=True - change one step with edit_skill instead of retyping the whole thing."""
    from nova.actions.knowledge import SECRET
    name = slug(name)
    if not name:
        return "Give the skill a short name, like 'movie-time'."
    description = re.sub(r"\s+", " ", str(description or "")).strip()[:300]
    body = str(steps or "").strip()
    if isinstance(steps, list):
        body = "\n".join(f"- {s}" for s in steps)
    if not body:
        return "The skill needs its steps."
    if len(body) > MAX_BODY:
        return f"That's too long for a skill (max {MAX_BODY} characters) - keep only the steps."
    if SECRET.search(body + " " + description):
        return "I won't save passwords, codes, keys, email addresses or phone numbers in a skill."
    when_ = _list(when) if not isinstance(when, list) else [str(w).strip() for w in when if str(w).strip()]
    nf = _list(not_for) if not isinstance(not_for, list) else [str(w).strip() for w in not_for if str(w).strip()]
    ra = _list(requires_any) if not isinstance(requires_any, list) else [str(w).strip() for w in requires_any if str(w).strip()]
    kind = str(kind or "").lower()
    if kind not in ("routine", "recipe", ""):
        kind = ""
    existed = (store.current().skills_dir / f"{name}.md").exists()
    if existed and not replace:
        return (f"I already have a skill called '{name}'. To change it use edit_skill (one step at a time), or save "
                "it again with replace=true to rewrite the whole thing.")
    v2_lines = any(ID_RE.match(l.strip()) or ATTR_RE.match(l.strip()) for l in body.splitlines())
    if not _steps(body) and len(body.splitlines()) > 1 and not v2_lines:
        body = "\n".join(f"- {l.strip()}" for l in body.splitlines() if l.strip())
    if not existed and not replace:
        sim = similar(description, when_, skip=name)
        if sim:
            return (f"That looks like my '{sim[0]}' skill already - shall I change that one with edit_skill instead? "
                    "(To keep both, save it again with replace=true.)")
        if len([s for s in load() if s["status"] == "active"]) >= MAX_ACTIVE:
            return f"I already have {MAX_ACTIVE} skills - delete one first ('{least_used()}' is the one I use least)."
    if kind in ("routine", ""):
        parsed, bad = parse_body_v2(body)
        if parsed is None and kind == "routine":
            return (f"That line isn't a step I can run: '{bad}'. Steps look like: open_app name=Spotify / "
                    "set_volume action=up, steps=5 / say Enjoy! / wait 2 / if page_has \"Log In\" goto s4. Or save it "
                    "as a recipe (plain steps).")
        if parsed is not None:
            for st in parsed:
                if "tool" in st and st["tool"] in LOCKED:
                    return f"A skill can't use {st['tool']}."
            text = render_v2({"name": name, "description": description, "examples": when_, "not_for": nf,
                              "requires_any": ra, "invoke": "explicit_only" if status == "draft" else "confirm_if_fuzzy",
                              "status": status}, parsed)
            ver = _write(name, text, author=author, cause=cause, op="replace" if existed else "create")
            s = get(name)
            how = f"say '{when_[0]}'" if when_ else f"ask for '{name.replace('-', ' ')}'"
            return (("Updated" if existed else "Saved") + f" the routine skill '{name}'"
                    + (f" with {len(s['steps'])} steps" if s else "") + f" (version {ver}). Next time just {how}.")
        kind = "recipe"
    text = _render(name, description, when_, "recipe", body)
    ver = _write(name, text, author=author, cause=cause, op="replace" if existed else "create")
    how = f"say '{when_[0]}'" if when_ else f"ask for '{name.replace('-', ' ')}'"
    return ("Updated" if existed else "Saved") + f" the recipe skill '{name}' (version {ver}). Next time just {how}."


def delete(name):
    """Move a skill to skills/.trash (not gone for good)."""
    s = get(name)
    if not s:
        return f"I have no skill called '{name}'."
    trash = store.current().skills_dir / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    shutil.move(s["file"], trash / f"{s['name']}-{int(time.time())}.md")
    _cache["key"] = None
    return f"Deleted the skill '{s['name']}' (a copy is in {trash})."


# ---- small edits

def _next_id(steps):
    nums = [int(s["id"][1:]) for s in steps if re.fullmatch(r"s\d+", s["id"])]
    return f"s{max(nums, default=0) + 1}"


def _ids_for(steps):
    """A format-1 routine's steps get ids s1..sN when it is first edited."""
    out = []
    for i, st in enumerate(steps, 1):
        out.append({**st, "id": st.get("id") or f"s{i}", "expect": st.get("expect"), "on_fail": st.get("on_fail"),
                    "note": st.get("note", "")})
    return out


def _step_from_op(op, known):
    text = str(op.get("step", "")).strip()
    text = ID_RE.sub(lambda m: m.group(2), text) if ID_RE.match(text) else text
    st = parse_step_v2(text, known)
    if st is None:
        return None, f"'{text}' isn't a step I can run"
    if "tool" in st and st["tool"] in LOCKED:
        return None, f"a skill can't use {st['tool']}"
    for t in st.get("tools", []):
        if t not in known or t in LOCKED:
            return None, f"{t} is not a tool I can use here"
    st.setdefault("expect", None)
    st.setdefault("on_fail", None)
    st.setdefault("note", "")
    if "expect" in op:
        st["expect"] = parse_check(op["expect"]) if op["expect"] else None
        if op["expect"] and not st["expect"]:
            return None, f"'{op['expect']}' isn't a check I know"
    if "on_fail" in op:
        st["on_fail"] = parse_on_fail(op["on_fail"]) if op["on_fail"] else None
        if op["on_fail"] and not st["on_fail"]:
            return None, f"'{op['on_fail']}' isn't a valid on_fail"
    return st, ""


def edit(name, ops, author="local", cause=""):
    """Change a skill with a few small, checked operations instead of rewriting it. Returns a message for the brain."""
    from nova.actions.knowledge import SECRET
    s = get(name)
    if not s:
        return f"I have no skill called '{name}'."
    if s["kind"] == "recipe":
        return (f"'{s['name']}' is a plain-English recipe, so there are no steps to change one by one. Save it again "
                "as a routine (replace=true) with steps I can run.")
    if s["kind"] == "broken":
        return f"'{s['name']}' is broken ({s['error']}). Fix its file or save it again with replace=true."
    if isinstance(ops, str):
        try:
            ops = json.loads(ops)
        except ValueError:
            return "ops must be a JSON list like [{\"op\": \"insert_step\", \"after\": \"s2\", \"step\": \"wait 2\"}]."
    if isinstance(ops, dict):
        ops = [ops]
    if not isinstance(ops, list) or not ops:
        return "Give me at least one change in ops."
    if len(ops) > 5:
        return "That's too many changes at once (max 5) - do them in a couple of goes."
    if SECRET.search(json.dumps(ops, ensure_ascii=False)):
        return "I won't save passwords, codes, keys, email addresses or phone numbers in a skill."
    known = _tool_names()
    steps = _ids_for([dict(st) for st in s["steps"]])
    meta = {k: list(s[k]) if isinstance(s[k], list) else s[k] for k in ("name", "description", "examples", "not_for",
                                                                       "requires_any", "invoke", "status")}
    done = []
    for op in ops:
        if not isinstance(op, dict):
            return "Each change must be an object with an \"op\"."
        kind = str(op.get("op", ""))
        ids = [x["id"] for x in steps]
        sid = str(op.get("id", "")).lower()
        if kind in ("replace_step", "delete_step", "set_expect", "set_on_fail") and sid not in ids:
            return f"There is no step {sid or '(missing id)'} in '{s['name']}' (steps: {', '.join(ids)}). Nothing was changed."
        if kind == "replace_step":
            st, err = _step_from_op(op, known)
            if not st:
                return f"{err}. Nothing was changed."
            old = steps[ids.index(sid)]
            st["id"] = sid
            st["expect"] = st["expect"] if "expect" in op else old.get("expect")
            st["on_fail"] = st["on_fail"] if "on_fail" in op else old.get("on_fail")
            steps[ids.index(sid)] = st
            done.append(f"changed {sid}")
        elif kind == "insert_step":
            after = str(op.get("after", "")).lower()
            if after not in ids + ["start", ""]:
                return f"There is no step {after} to insert after. Nothing was changed."
            st, err = _step_from_op(op, known)
            if not st:
                return f"{err}. Nothing was changed."
            st["id"] = _next_id(steps)
            steps.insert(0 if after in ("start", "") else ids.index(after) + 1, st)
            done.append(f"added {st['id']}")
        elif kind == "delete_step":
            if any(sid == x.get("goto") or sid in [a.get("id") for a in (x.get("on_fail") or [])] for x in steps):
                return f"Another step jumps to {sid}, so I can't delete it. Nothing was changed."
            steps = [x for x in steps if x["id"] != sid]
            done.append(f"removed {sid}")
        elif kind == "set_expect":
            chk = parse_check(op.get("expect", "")) if op.get("expect") else None
            if op.get("expect") and not chk:
                return f"'{op['expect']}' isn't a check I know. Nothing was changed."
            steps[ids.index(sid)]["expect"] = chk
            done.append(f"{sid} now checks {render_check(chk) if chk else 'nothing'}")
        elif kind == "set_on_fail":
            acts = parse_on_fail(op.get("on_fail", "")) if op.get("on_fail") else None
            if op.get("on_fail") and not acts:
                return f"'{op['on_fail']}' isn't a valid on_fail. Nothing was changed."
            steps[ids.index(sid)]["on_fail"] = acts
            done.append(f"{sid} on_fail updated")
        elif kind in ("add_example", "add_not_for"):
            text = re.sub(r"\s+", " ", str(op.get("text", "")).replace(",", " ")).strip()
            key = "examples" if kind == "add_example" else "not_for"
            if not text:
                return "That change needs some text. Nothing was changed."
            if text.lower() not in [x.lower() for x in meta[key]]:
                meta[key].append(text)
            done.append(f"{kind[4:]} '{text}'")
        elif kind == "set_description":
            meta["description"] = re.sub(r"\s+", " ", str(op.get("text", ""))).strip()[:300]
            done.append("new description")
        else:
            return (f"I don't know the change '{kind}'. Use replace_step, insert_step, delete_step, set_expect, "
                    "set_on_fail, add_example, add_not_for or set_description. Nothing was changed.")
    ids = [x["id"] for x in steps]
    for i, st in enumerate(steps):                       # the result must still be a skill that runs
        for t in ([st["goto"]] if "goto" in st else []) + [a["id"] for a in (st.get("on_fail") or []) if a["do"] == "goto"]:
            if t not in ids or ids.index(t) <= i:
                return f"That would leave step {st['id']} jumping to {t}, which isn't ahead of it. Nothing was changed."
    text = render_v2({**meta, "risk_explicit": _explicit_risk(s["name"])}, steps)
    check = parse(text, s["name"])
    if not check or check["kind"] == "broken":
        return f"That would break the skill ({check['error'] if check else 'empty'}). Nothing was changed."
    ver = _write(s["name"], text, author=author, cause=cause, op="; ".join(done))
    return (f"Changed '{s['name']}' (now version {ver}): {', '.join(done)}. Say 'undo that skill change' to go back.")


def describe():
    skills = load()
    if not skills:
        return (f"I have no skills yet. Teach me one - e.g. 'when I say movie time, close everything, open Netflix "
                f"and turn the volume up' - and I'll save it in {store.current().skills_dir}.")
    lines = []
    for s in skills:
        tag = "BROKEN - " + s["error"] if s["kind"] == "broken" else s["kind"]
        st = stats_get(s["name"])
        extra = ""
        if s["status"] != "active":
            extra += f" [{s['status']}]"
        if st.get("runs"):
            extra += f" [{st.get('ok', 0)}/{st['runs']} ok]"
        lines.append(f"- {s['name']} ({tag}): {s['description'] or s['body'][:80]}" + extra
                     + (f" [say: {', '.join(s['examples'][:3])}]" if s["examples"] else ""))
    return f"My skills (files in {store.current().skills_dir}):\n" + "\n".join(lines)


def fill(args, text):
    """Replace {input} (as said) and {input_url} (made safe for a web address) in a step's args."""
    from urllib.parse import quote_plus

    def sub(v):
        return v.replace("{input_url}", quote_plus(text)).replace("{input}", text) if isinstance(v, str) else v
    return {k: sub(v) for k, v in args.items()}


# ------------------------------------------------------------------ library health

def similar(description, examples, skip=""):
    """An existing skill that is nearly the same thing as this one (name, score), or None."""
    mine = f"{description}. {' '.join(examples)}".strip()
    best = (None, 0.0)
    for s in load():
        if s["name"] == skip or s["kind"] == "broken":
            continue
        sc = retrieval.scores(mine, [f"{s['description']}. {' '.join(s['examples'])}"])[0]
        if sc > best[1]:
            best = (s["name"], sc)
    bar = 0.9 if retrieval.ready() else 0.8
    return best if best[0] and best[1] >= bar else None


def least_used():
    """The active skill used least (for when the library is full)."""
    act = [s for s in load() if s["status"] == "active"]
    if not act:
        return None
    return min(act, key=lambda s: (stats_get(s["name"]).get("runs", 0), stats_get(s["name"]).get("last_used", 0)))["name"]


# ------------------------------------------------------------------ skills from what Nova really did

SKIP_IN_TRACE = {"more_tools", "escalate", "list_skills", "react", "new_move", "go_to_sleep", "remember_fact",
                 "update_profile", "learn_lesson", "recall_knowledge", "forget_fact", "forget_move", "change_look",
                 "get_datetime", "background_jobs", "stop_background_job"}
LOOKS = {"look_at_screen", "look_through_camera", "list_clickable", "read_page", "take_screenshot", "list_windows",
         "read_selection", "get_clipboard", "get_system_status", "list_notes", "read_notifications"}


def steps_from_trace(trace, goal=""):
    """The tool calls of a finished task -> clean format-2 steps, or (None, why not). Only steps that worked; looks
    that only helped her find the way are dropped (a final look becomes a report); a click at screen coordinates
    can't be replayed, so it becomes one small brain step; the same step twice in a row is kept once."""
    from nova.actions.knowledge import SECRET
    good = [t for t in trace if t.get("ok") and t["tool"] not in SKIP_IN_TRACE and t["tool"] not in LOCKED]
    while good and good[0]["tool"] in LOOKS and len(good) > 1:
        good = good[1:]
    last_look = bool(good) and good[-1]["tool"] in LOOKS
    steps, prev = [], None
    for i, t in enumerate(good):
        if t["tool"] in LOOKS and not (last_look and i == len(good) - 1):
            continue
        if t["tool"] == "click_at":
            st = {"brain": f"Click the on-screen item this task needs (task: {goal[:100]})", "tools": ["list_clickable", "click_element"], "slot": ""}
        elif t["tool"] == "_brain":          # from a recording: something to do that can't be written down exactly
            st = {"brain": str(t["args"].get("instr", ""))[:200], "tools": list(t["args"].get("tools") or []), "slot": ""}
        else:
            st = {"tool": t["tool"], "args": dict(t["args"])}
        key = json.dumps(st, sort_keys=True, default=str)
        if key == prev:
            continue
        prev = key
        steps.append(st)
    if not steps:
        return None, "There was nothing in that task worth saving as steps."
    if last_look:
        steps.append({"report": "Tell the user briefly what you found."})
    steps = steps[:MAX_STEPS - 1]
    if SECRET.search(json.dumps(steps, ensure_ascii=False)):
        return None, "That task had a password, code, key or personal number in it, so I won't save it as a skill."
    for n, st in enumerate(steps, 1):
        st.update(id=f"s{n}", expect=None, on_fail=None, note="")
    return steps, ""


def draft_from_steps(name, description, examples, steps, hints=None, goal=""):
    """Save steps Nova really did as a DRAFT skill (only runs when asked for by name; it becomes a real skill after
    it has worked twice - see record_run). `hints` (from the cloud brain, all optional and checked here):
    {"slots": [{"step","arg","find"}], "expect": {id: check text}, "not_for": [text]}. Returns a message."""
    from nova.actions.knowledge import SECRET
    name = slug(name)
    if not name:
        return "Give the skill a short name, like 'crunchyroll-new'."
    if (store.current().skills_dir / f"{name}.md").exists():
        return f"I already have a skill called '{name}' - pick another name, or change it with edit_skill."
    ex = [e for e in (_list(examples) if not isinstance(examples, list) else examples) if str(e).strip()] or [name.replace("-", " ")]
    description = re.sub(r"\s+", " ", str(description or "")).strip()[:300] or f"{name.replace('-', ' ')} (saved from a task: {goal[:80]})"
    if SECRET.search(description + " ".join(ex)):
        return "I won't save passwords, codes, keys, email addresses or phone numbers in a skill."
    if len([s for s in load() if s["status"] == "active"]) >= MAX_ACTIVE:
        return (f"I already have {MAX_ACTIVE} skills. Delete or retire one first - '{least_used()}' is the one I use least.")
    sim = similar(description, ex)
    if sim:
        return f"That looks like my '{sim[0]}' skill already - shall I change that one with edit_skill instead?"
    by_id = {s["id"]: s for s in steps}
    hints = hints if isinstance(hints, dict) else {}
    for sl in hints.get("slots", []) if isinstance(hints.get("slots"), list) else []:
        if not isinstance(sl, dict):
            continue
        st = by_id.get(str(sl.get("step", "")).lower())
        arg, find = sl.get("arg"), str(sl.get("find", ""))
        if st and "tool" in st and find and isinstance(st["args"].get(arg), str) and find in st["args"][arg]:
            st["args"][arg] = st["args"][arg].replace(find, "{input}")
    exp = hints.get("expect") if isinstance(hints.get("expect"), dict) else {}
    for sid, text in exp.items():
        chk = parse_check(text)
        if chk and str(sid).lower() in by_id and "tool" in by_id[str(sid).lower()]:
            by_id[str(sid).lower()]["expect"] = chk
    nf = [str(x) for x in hints.get("not_for", []) if isinstance(x, str)][:8] if isinstance(hints.get("not_for"), list) else []
    meta = {"name": name, "description": description, "examples": ex, "not_for": nf, "requires_any": [],
            "invoke": "explicit_only", "status": "draft"}
    text = render_v2(meta, steps)
    chk = parse(text, name)
    if not chk or chk["kind"] == "broken":
        return f"I couldn't turn that task into a skill ({chk['error'] if chk else 'empty'})."
    _write(name, text, author="recorder", cause=goal[:200], op="create from a finished task")
    return (f"Saved a draft skill '{name}' with {len(steps)} steps: " + "; ".join(render_step(s)[:50] for s in steps[:5])
            + f". Say '{ex[0]}' to try it - after it works twice I'll keep it for good.")
