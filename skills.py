"""Skills: new abilities for Nova written as small text files instead of Python.

Each skill is a Markdown file in skills/ (NOVA_SKILLS points elsewhere; the tests use a temp folder):

    ---
    name: work-mode
    description: Get the laptop ready for work
    when: start work, work mode, let's get going
    kind: routine
    ---
    - minimize_all_windows
    - open_app name="Visual Studio Code"
    - open_website url_or_search=mail.google.com
    - set_dark_mode enabled=true
    - say Ready when you are, boss!

Two kinds:
  routine - every line is a step: a tool call (`tool key=value, key=value` or `tool {"json": "args"}`),
            `say <text>` or `wait <seconds>`. app.py runs the steps itself, one after another, without asking the
            brain - reliable even with the small local model. `{input}` in a step is replaced by run_skill's input
            ("search youtube for cats" -> input "cats"); `{input_url}` is the same, made safe for a web address.
  recipe  - plain-English instructions. They go into the prompt when the request matches, and the brain follows
            them with its tools as usual. Good for things that need judgement ("write my weekly report").
Without `kind:`, a body whose every line is a valid step is a routine; anything else is a recipe.

Skills only combine Nova's existing tools, so every step still goes through the same approval rules as a direct
call (app.is_risky) - a skill can never get around the "ask first" step.
"""
import json
import os
import re
import shutil
import time
from pathlib import Path

import retrieval
from shared import ROOT

DIR = Path(os.environ.get("NOVA_SKILLS") or ROOT / "skills")
MAX_STEPS = 25
MAX_BODY = 2500
STEP_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s*(.+?)\s*$")
NAME_RE = re.compile(r"[^a-z0-9]+")
KV_RE = re.compile(r'(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|\'[^\']*\'|[^,]*)')


def slug(name):
    return NAME_RE.sub("-", str(name or "").lower()).strip("-")[:40]


# ------------------------------------------------------------------ parsing

def _tool_names():
    import tools
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
    if name not in (known or _tool_names()) or name in ("run_skill", "save_skill", "delete_skill", "more_tools"):
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
    """The steps of a routine body, or (None, the first line that isn't a step)."""
    known = _tool_names()
    lines = _steps(body) or [l.strip() for l in body.splitlines() if l.strip()]
    out = []
    for l in lines:
        s = parse_step(l, known)
        if s is None:
            return None, l
        out.append(s)
    return out[:MAX_STEPS], None


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


def parse(text, fallback_name=""):
    meta, body = _front(text)
    name = slug(meta.get("name") or fallback_name)
    if not name or not body:
        return None
    kind = meta.get("kind", "").lower()
    steps, bad = parse_routine(body) if kind != "recipe" else (None, None)
    if kind != "routine" and steps is None:
        kind = "recipe"
    elif steps is not None:
        kind = "routine"
    else:
        kind = "broken"
    tools_ = _list(meta.get("tools"))
    if kind == "routine":
        tools_ = list(dict.fromkeys(s["tool"] for s in steps if "tool" in s))
    elif kind == "recipe":
        known = _tool_names()
        tools_ = [t for t in dict.fromkeys(tools_ + re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", body)) if t in known]
    return {"name": name, "description": meta.get("description", ""), "when": _list(meta.get("when")),
            "kind": kind, "body": body[:MAX_BODY], "steps": steps or [], "tools": tools_,
            "error": f"this line is not a step: {bad}" if kind == "broken" else "",
            "needs_input": "{input" in body}


# ------------------------------------------------------------------ the folder

_cache = {"key": None, "skills": []}


def load():
    """All skills, re-read only when a file in the folder changed."""
    try:
        files = sorted(DIR.glob("*.md"))
        key = tuple((f.name, f.stat().st_mtime_ns) for f in files)
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


def _texts(s):
    base = f"{s['name'].replace('-', ' ')}: {s['description']}"
    return [base] + [w for w in s["when"]]


def match(query):
    """The skill this request is about, or None. A trigger phrase or the skill's name in the request always wins."""
    q = " " + re.sub(r"[^a-z0-9 ]+", " ", (query or "").lower()) + " "
    skills = [s for s in load() if s["kind"] != "broken"]
    if not skills or not q.strip():
        return None
    for s in skills:
        phrases = [s["name"].replace("-", " ")] + s["when"]
        for p in phrases:
            p = re.sub(r"[^a-z0-9 ]+", " ", p.lower()).strip()
            if p and f" {p} " in re.sub(r"\s+", " ", q):
                return s
    texts, owner = [], []
    for s in skills:
        for t in _texts(s):
            texts.append(t)
            owner.append(s)
    scores = retrieval.scores(query, texts)
    best = max(range(len(texts)), key=lambda i: scores[i])
    bar = 0.75 if retrieval.ready() else 0.6
    return owner[best] if scores[best] >= bar else None


def guess_input(skill, query):
    """For a routine with {input}: the part of the request after the trigger phrase ("play lofi music" -> "lofi music")."""
    low = query.lower()
    for p in sorted([skill["name"].replace("-", " ")] + skill["when"], key=len, reverse=True):
        i = low.find(p.lower())
        if p and i >= 0:
            rest = query[i + len(p):].strip(" ,.:-")
            rest = re.sub(r"^(for|about|of|to|on)\s+", "", rest, flags=re.I)
            if rest:
                return rest
    return ""


def prompt_block(query, matched=None):
    """What goes into the prompt about skills for this request: the matching one in full, and the names of the rest."""
    skills = [s for s in load() if s["kind"] != "broken"]
    if not skills:
        return ""
    lines = []
    if matched:
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


# ------------------------------------------------------------------ writing skills

def _render(name, description, when, kind, body):
    return (f"---\nname: {name}\ndescription: {description}\n" + (f"when: {', '.join(when)}\n" if when else "")
            + f"kind: {kind}\n---\n{body.strip()}\n")


def save(name, description, steps, when="", kind=""):
    """Write (or replace) a skill file. Returns a message for the brain."""
    from knowledge import SECRET
    name = slug(name)
    if not name:
        return "Give the skill a short name, like 'movie-time'."
    description = re.sub(r"\s+", " ", str(description or "")).strip()[:200]
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
    kind = str(kind or "").lower()
    if kind not in ("routine", "recipe", ""):
        kind = ""
    if not _steps(body) and len(body.splitlines()) > 1:
        body = "\n".join(f"- {l.strip()}" for l in body.splitlines() if l.strip())
    if kind in ("routine", ""):
        parsed, bad = parse_routine(body)
        if parsed is None and kind == "routine":
            return (f"That line isn't a step I can run: '{bad}'. Routine steps look like: open_app name=Spotify / "
                    "set_volume action=up, steps=5 / say Enjoy! / wait 2. Or save it as a recipe (plain steps).")
        kind = "routine" if parsed is not None else "recipe"
    DIR.mkdir(parents=True, exist_ok=True)
    f = DIR / f"{name}.md"
    existed = f.exists()
    f.write_text(_render(name, description, when_, kind, body), encoding="utf-8")
    s = get(name)
    how = f"say '{when_[0]}'" if when_ else f"ask for '{name.replace('-', ' ')}'"
    return (("Updated" if existed else "Saved") + f" the {kind} skill '{name}'"
            + (f" with {len(s['steps'])} steps" if s and kind == "routine" else "") + f". Next time just {how}.")


def delete(name):
    """Move a skill to skills/.trash (not gone for good)."""
    s = get(name)
    if not s:
        return f"I have no skill called '{name}'."
    trash = DIR / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    shutil.move(s["file"], trash / f"{s['name']}-{int(time.time())}.md")
    return f"Deleted the skill '{s['name']}' (a copy is in {trash})."


def describe():
    skills = load()
    if not skills:
        return (f"I have no skills yet. Teach me one - e.g. 'when I say movie time, close everything, open Netflix "
                f"and turn the volume up' - and I'll save it in {DIR}.")
    lines = []
    for s in skills:
        tag = "BROKEN - " + s["error"] if s["kind"] == "broken" else s["kind"]
        lines.append(f"- {s['name']} ({tag}): {s['description'] or s['body'][:80]}"
                     + (f" [say: {', '.join(s['when'][:3])}]" if s["when"] else ""))
    return f"My skills (files in {DIR}):\n" + "\n".join(lines)


def fill(args, text):
    """Replace {input} (as said) and {input_url} (made safe for a web address) in a step's args."""
    from urllib.parse import quote_plus

    def sub(v):
        return v.replace("{input_url}", quote_plus(text)).replace("{input}", text) if isinstance(v, str) else v
    return {k: sub(v) for k, v in args.items()}
