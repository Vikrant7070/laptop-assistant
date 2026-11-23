"""Nova the manager, step 4a (docs/plans/nova-manager-plan.md section 6): understand -> ask -> brief.

Code does the managing; the small local brain only fills gaps. Pure code here - no app parts, no brain calls:

    triage(text)            -> "research" / "coding" / "research_coding" / "laptop" (fixed word rules, nova/intents)
    plan(request, kind)     -> the job dict: kind, website?, size, type, worker (+ why), the questions still to ask
    choose(job)             -> 4b: the worker from the job history - the cheapest one that has done this type of job
                               before; Claude Code for big / new jobs or after OpenCode failed it twice (only
                               suggested: the Go card is where the user says yes)
    questions(job)          -> fixed lists, each question with a fact key and its one-line reason; one is skipped when
                               the request already answers it or the user answered it before (facts); at most
                               MAX_QUESTIONS per job
    brief(job)              -> the fixed template, filled by code (never by the local brain)
    remember / recall       -> answers saved as unconfirmed facts (data/facts.json, kind "answer"), shown on the
                               Knowledge page with ✓ / ×, so the same question isn't asked again

The app side (questions as cards, the brief card, Go / Change it, starting the worker) is nova/core/app/managing.py.
"""
import re
import time

from nova import intents, task_state
from nova.actions import knowledge, learner, workers

MAX_QUESTIONS = 3            # the user's rule: questions one at a time, only when the answer changes the work
KINDS = ("research", "coding", "research_coding", "laptop")
WORKER_LABEL = {"opencode": "OpenCode, free", "claude": "Claude Code, paid", "research": "Research, free"}

STACKS = ["Python", "HTML + CSS + JavaScript", "React", "Node.js"]
PAGES = [("home", "Home"), ("docs", "Docs"), ("download", "Download"), ("contact", "Contact")]
STYLES = [("clean", "Clean and simple"), ("nova", "Like Nova's own look (dark, purple)"), ("playful", "Playful and bright")]
PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|~[\\/]|\\\\)[^\s\"'<>|?*]+")


# ------------------------------------------------------------------ understand

def triage(text):
    """The kind of request, by fixed word rules. None = the rules can't tell (the brain's hand_off_job says)."""
    t = text or ""
    if intents.RESEARCH_THEN_BUILD.search(t):
        return "research_coding"
    if intents.BUILD_THING.search(t) or intents.PROJECT_CODE.search(t):
        return "coding"
    return None


def clear_hand_off(text):
    """A request clear enough to hand off without a brain turn (body.answer's code route): building a site / app /
    page, a change in "my project", or research and then building. Plain "research X" keeps its own path."""
    return triage(text)


def plan(request, kind):
    """The job before any question: what it is, how big, who'd do it."""
    kind = kind if kind in KINDS else (triage(request) or "coding")
    website = kind != "research" and bool(intents.WEBSITE.search(request))
    new = bool(intents.BUILD_THING.search(request))
    size = "big" if new or intents.BIG_JOB.search(request) else "small"
    job = {"id": f"m{time.time():.6f}", "request": " ".join(str(request).split())[:500],     # the user's own words
           "kind": kind, "website": website, "new": new, "size": size, "answers": {}, "changes": [],
           "folder": _path_in(request), "asked": 0}
    job["type"] = job_type(job)
    job["worker"], job["reason"] = choose(job)
    every = questions(job, limit=None)
    job["queue"] = every[:MAX_QUESTIONS]
    for q in every[MAX_QUESTIONS:]:              # past the limit: not asked - her guess goes in the brief, marked
        if q.get("guess") not in (None, "", []):
            job["answers"][q["key"]] = {"value": q["guess"], "how": "unasked"}
    return job


# ------------------------------------------------------------------ 4b: which worker, from the job history

JOB_TYPES = ("readme", "small fix", "feature", "new site", "new app", "research")
PLURAL = {"readme": "README jobs", "small fix": "small fixes", "feature": "features", "new site": "new sites",
          "new app": "new apps", "research": "research jobs"}
RECENT = 5                   # the tries that count for "most recent tries worked"


def job_type(job):
    if job["kind"] == "research":
        return "research"
    if job["new"]:
        return "new site" if job["website"] else "new app"
    if intents.README.search(job["request"]):
        return "readme"
    return "feature" if job["size"] == "big" else "small fix"


def track_record(jtype, recs=None):
    """{worker: {"tried", "worked", "committed", "last": [worked?, ...] newest first}} over the RECENT newest
    records of this job type. Worked = finished and changed files; committed = the user accepted it."""
    out = {}
    for r in reversed(recs if recs is not None else workers.load_jobs()):
        if r.get("job_type") != jtype or r.get("status") not in ("done", "needs-user", "failed"):
            continue
        h = out.setdefault(r.get("worker"), {"tried": 0, "worked": 0, "committed": 0, "last": []})
        if h["tried"] >= RECENT:
            continue
        ok = r.get("status") in ("done", "needs-user") and bool(r.get("files"))
        h["tried"] += 1
        h["worked"] += ok
        h["committed"] += bool(r.get("committed"))
        h["last"].append(ok)
    return out


def choose(job, recs=None):
    """(worker, why) for this job. Never escalates by itself: Claude Code after OpenCode's failures is a suggestion
    the Go card shows (with its cost), and the other worker is one tap away."""
    if job["kind"] == "research":
        return "research", "Research is free and reads the web for you."
    t = job["type"]
    if t in ("new site", "new app", "feature"):
        return "claude", f"A {t} is a big job, so the strongest worker gets it."
    h = track_record(t, recs).get("opencode")
    if not h:
        return "opencode", f"A {t} - the free worker first (it hasn't done one before)."
    done = f"OpenCode did {h['worked']} of {h['tried']} {PLURAL[t]}" + (f" ({h['committed']} committed)" if h["committed"] else "")
    if h["last"][:2] == [False, False]:
        return "claude", f"{done}, and the last 2 failed - Claude Code might do better."
    if (h["worked"] + h["committed"]) * 2 >= h["tried"] + h["committed"]:      # a commit counts double
        return "opencode", done + "."
    return "opencode", done + " - still the free one first; you can switch."


def other_worker(worker):
    return {"opencode": "claude", "claude": "opencode"}.get(worker)


def research_topic(request):
    """'research the best chart library, then add it' -> 'the best chart library'."""
    m = re.match(r"\W*(?:research|look up|find out|compare)\s+(.+?)[,;]?\s+(?:and then|then|after that)\b", request, re.I)
    return (m.group(1) if m else request).strip()


def _secret(text):
    return bool(task_state._SECRET_RE.search(text or ""))


def _path_in(text):
    m = PATH_RE.search(text or "")
    return m.group(0).rstrip(".,;:)") if m else ""


HAND_OFF = None             # set by app.managing: the flow behind the hand_off_job tool


def hand_off(request, kind=""):
    """The hand_off_job tool: start the manager's questions (app.managing.from_tool)."""
    if HAND_OFF is None:
        return "The manager isn't available here - use start_coding_job or research_in_background."
    return HAND_OFF(request, kind)


# ------------------------------------------------------------------ ask

def known_folders(limit=4):
    """Project folders from past coding jobs and earlier answers, newest first."""
    seen, out = set(), []
    folders = [r.get("folder") for r in reversed(workers.load_jobs()) if r.get("folder")]
    folders = [recall("project_folder")] + folders if recall("project_folder") else folders
    for f in folders:
        key = str(f).lower().rstrip("\\/")
        if key not in seen:
            seen.add(key)
            out.append(str(f))
    return out[:limit]


def _q(key, text, kind, choices=(), why="", other=False, guess=None, last=None, fact=""):
    return {"key": key, "text": text, "kind": kind, "choices": list(choices), "why": why, "other": other,
            "guess": guess, "last": last, "fact": fact}


def questions(job, limit=MAX_QUESTIONS):
    """The questions still worth asking, in order, at most `limit` (None = all). Each is a plain dict
    (asking.question's arguments); one the request or an earlier answer covers never makes it here."""
    req = job["request"]
    out = []
    if job["kind"] == "research":
        out.append(_q("focus", "What should the research focus on?", "text", guess="",
                      why="So the report answers what you actually want to know."))
        return out[:limit]
    if not job["folder"]:
        folders = known_folders()
        last = recall("project_folder")
        if folders:
            out.append(_q("folder", "Which project folder?", "one", [(f, _short_path(f)) for f in folders],
                          why="So the worker changes the right project.", other=True, guess=folders[0],
                          last=last if last in folders else None, fact="project_folder"))
        else:
            out.append(_q("folder", "Which folder should it work in? (the full path)", "text",
                          why="So the worker changes the right project.", fact="project_folder"))
    if job["website"]:                              # what the site has changes the work most: asked before the rest
        out.append(_q("pages", "What should the site have?", "many", PAGES, why="So the brief lists every page.",
                      other=True, guess=["home"], last=None))
    if job["new"] and not intents.NEW_OR_EXISTING.search(req):
        out.append(_q("new", "A new project, or a change to an existing one?", "yesno",
                      [("new", "A new project"), ("existing", "Change the existing one")],
                      why="A new project gets set up from scratch; an existing one is changed carefully.", guess="new"))
    if not intents.STACK_NAMED.search(req) and not recall("stack"):
        default = "HTML + CSS + JavaScript" if job["website"] else "Python"
        out.append(_q("stack", "Which language or stack?", "one", [(s, s) for s in STACKS],
                      why="So the worker writes it in what you use.", other=True, guess=default, fact="stack"))
    if job["website"] and not recall("site_style"):
        out.append(_q("style", "A style to follow?", "one", STYLES, why="So it looks the way you like.",
                      other=True, guess="clean", fact="site_style"))
    return out[:limit]


def _short_path(p):
    p = str(p).rstrip("\\/")
    parts = re.split(r"[\\/]", p)
    return parts[-1] + f"  ({p})" if len(parts) > 1 and len(p) > len(parts[-1]) else p


# ------------------------------------------------------------------ answers -> facts (unconfirmed)

def remember(key, value, now=None):
    """Save an answer as an unconfirmed fact (one per key: the newest answer wins)."""
    if not key or value in (None, "", []) or _secret(str(value)):
        return None
    now = now or time.time()
    value = value if isinstance(value, str) else ", ".join(map(str, value))
    with learner._lock:
        d = learner.load_facts()
        f = next((x for x in d["facts"] if x.get("kind") == "answer" and x.get("key") == key), None)
        if f is None:
            f = {"id": d["next_id"], "kind": "answer", "key": key, "confirmed": False, "learned": now}
            d["next_id"] += 1
            d["facts"].append(f)
        elif f.get("value") != value:
            f["confirmed"] = False            # a changed answer needs the ✓ again
        f.update(value=value[:300], last=now)
        d["facts"] = sorted(d["facts"], key=lambda x: x.get("last", 0))[-learner.MAX_FACTS:]
        learner._save_facts(d)
        return f


def recall(key):
    """The saved answer for a fact key, or ""."""
    f = next((x for x in learner.load_facts()["facts"] if x.get("kind") == "answer" and x.get("key") == key), None)
    return f.get("value", "") if f else ""


FACT_NAMES = {"project_folder": "your project folder", "stack": "the language / stack you use",
              "site_style": "the style you like for sites"}


def describe_answer(f):
    return f"You told me {FACT_NAMES.get(f.get('key'), f.get('key'))}: {f.get('value')}."


# ------------------------------------------------------------------ brief

def _answer_line(job, key, label, fmt=str):
    a = job["answers"].get(key)
    if not a:
        return ""
    v = a["value"]
    text = fmt(v) if v not in (None, "", []) else "(none given)"
    return f"- {label}: {text}" + {"skip": "  (my guess - the user skipped this)",
                                   "unasked": "  (my guess - not asked)"}.get(a.get("how"), "")


def about_user():
    """What a worker needs to know about the user: the stack and style they use (their answers) and their work.
    Not their preferences for Nova herself (voice, personality), and nothing secret."""
    p = knowledge.load()["profile"]
    lines = []
    for key in ("stack", "site_style"):
        v = recall(key)
        if v:
            lines.append(f"- {FACT_NAMES[key][0].upper() + FACT_NAMES[key][1:]}: {v}")
    if p.get("work"):
        lines.append(f"- Work: {p['work']}")
    return [task_state.clean(x, 200) for x in lines if not _secret(x)]


def brief(job):
    """The worker's brief, from the fixed template (plan 4a step 3)."""
    labels = dict(PAGES)
    req = [f"- {job['request']}"]
    req += [x for x in (
        _answer_line(job, "new", "Project", lambda v: "a new project" if v == "new" else "change the existing project"),
        _answer_line(job, "stack", "Stack"),
        _answer_line(job, "pages", "Pages", lambda v: ", ".join(labels.get(x, x) for x in (v if isinstance(v, list) else [v]))),
        _answer_line(job, "style", "Style", lambda v: dict(STYLES).get(v, v)),
        _answer_line(job, "focus", "Focus"),
    ) if x]
    req += [f"- Change asked by the user: {c}" for c in job["changes"]]
    done = ["- The project's tests pass, if it has any (pytest / npm test)."]
    if job["website"]:
        done.append("- Every page listed above exists and opens in a browser.")
    if job["kind"] == "research":
        done = ["- A short report that answers the question, with at least 2 sources cited."]
    folder = folder_of(job)
    parts = [f"GOAL (the user's words): {job['request']}"]
    about = about_user()
    if about:
        parts.append("ABOUT THE USER:\n" + "\n".join(about))
    parts.append("REQUIREMENTS:\n" + "\n".join(req))
    if job["kind"] != "research":
        guessed = (job["answers"].get("folder") or {}).get("how") == "skip"
        parts.append(f"FOLDER: {folder or '(not given)'}" + ("  (my guess - the user skipped this)" if guessed else ""))
    if job["kind"] == "research_coding" and job.get("found"):
        parts.append("WHAT THE RESEARCH FOUND (use it):\n" + job["found"])
    elif job["kind"] == "research_coding":
        parts.insert(0, f"STEPS: 1. Research: {research_topic(job['request'])} (the research worker). "
                        "2. Coding: the rest of this brief, with what the research found added.")
    parts.append("DONE MEANS:\n" + "\n".join(done))
    parts.append("MUST NOT: commit, push, publish, deploy, delete files or pay for anything - the user decides "
                 "after looking at the changes.")
    return "\n\n".join(parts)


def folder_of(job):
    a = job["answers"].get("folder")
    return (a["value"] if a and a.get("value") else "") or job["folder"]


def worker_label(w):
    if w == "claude":
        budget = workers.config()["claude"].get("budget_usd") or 1.0
        return f"Claude Code, paid, up to ${float(budget):.2f}"
    return WORKER_LABEL.get(w, w)


def go_label(job):
    if job["kind"] == "research_coding" and not job.get("found"):
        return f"Go (research first, then {worker_label(job['worker'])})"
    return f"Go ({worker_label(job['worker'])})"


def switch_label(job):
    alt = other_worker(job["worker"])
    return f"Use {workers.NAMES[alt].split(' +')[0]} instead ({worker_label(alt)})" if alt else ""


def needs_go(job, mode):
    """The go-ahead setting (⚙ Workers → Manager): always (default) / paid (only Claude Code asks) / never."""
    if mode == "never":
        return False
    if mode == "paid":
        return job["worker"] == "claude"
    return True
