"""Skill files as text: limits, parsing (format 1 and 2), the front matter, risk, and rendering back to text."""
import json
import re
from nova import store


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
