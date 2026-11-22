"""The episode log and the automatic Learner (manager plan, phase 2).

Episode log (episodes table, data/nova.db): one row per finished task - the goal, the steps (tool, arguments with
anything secret-looking hidden, worked or not), the Judge's verdict, time taken, the brain, background jobs it
started and how they ended. Raw episodes are deleted after KEEP_DAYS; what was learned from them stays.

Learner (learn(), run by app.py at idle time - never during a task). Pure code, no brain calls:
  - A task that worked (the Judge said done, or its research job finished) and isn't a skill already -> one question
    on the daily list: "save it as a skill?" (only for tasks with 2+ replayable steps - one step is just a request).
  - The same kind of task done twice with one changing part (search for tea / search for coffee) -> "save it as a
    skill with the changing part as {input}?" (the two episodes are compared; the differing argument is the slot).
  - Research jobs -> facts (data/facts.json), learned silently: which sites' pages ended up cited in the report and
    which were opened and not used. The next research on the same topic reads them first (sites_for).

Learning without asking: facts only (viewable and deletable on the Knowledge page, "unconfirmed" until the user
confirms one). New skills: always asked. A wrong verdict would teach a wrong skill, so "worked" is strict (worked()):
a task no check could look at never becomes a skill offer.
"""
import json
import re
import threading
import time
import urllib.parse

from nova.log import log_exception
from nova import db
from nova import store

KEEP_DAYS = 30
MAX_EPISODES = 2000
MAX_FACTS = 300
_lock = threading.RLock()

# Tools that are about Nova herself, not the task (the same list skills.steps_from_trace drops, plus the locked ones).
_SKIP = {"more_tools", "escalate", "list_skills", "react", "new_move", "go_to_sleep", "remember_fact", "update_profile",
         "learn_lesson", "recall_knowledge", "forget_fact", "forget_move", "change_look", "get_datetime",
         "background_jobs", "stop_background_job", "run_skill", "save_skill", "delete_skill", "edit_skill",
         "undo_skill_change", "save_task_as_skill", "start_watching", "stop_watching"}
JOB_RE = re.compile(r"background job #(\d+)", re.I)
HIDDEN = "[hidden]"
STOPWORDS = {"a", "an", "the", "of", "for", "on", "in", "to", "and", "or", "about", "with", "is", "are", "what", "how",
             "me", "my", "best", "some", "find", "search", "research", "look", "up", "into", "please", "nova", "can",
             "you", "out"}


def _secret():
    from nova.actions.knowledge import SECRET
    return SECRET


def _redact(value):
    """Arguments go to disk: anything that looks like a password, code, key, email or phone number is hidden."""
    if isinstance(value, str):
        return HIDDEN if _secret().search(value) else value
    if isinstance(value, dict):
        return {k: (HIDDEN if _secret().search(str(k)) and isinstance(v, str) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


# ------------------------------------------------------------------ the episode log

def episode_from_task(task, brain="", now=None):
    """A finished task (app.state['task']) -> an episode dict, or None when it did nothing worth keeping."""
    if not task or not task.get("goal"):
        return None
    now = now or time.time()
    steps, jobs = [], []
    ran_skill = task.get("ran_skill") or ""      # app.run_skill sets it (run_skill itself is never traced)
    for t in task.get("trace") or []:
        if t.get("tool") in _SKIP:
            continue
        args = _redact(dict(t.get("args") or {}))
        steps.append({"tool": t["tool"], "args": args, "ok": bool(t.get("ok"))})
        m = JOB_RE.search(str(t.get("out") or ""))
        if m and t.get("ok"):
            jobs.append({"id": int(m.group(1)), "status": "working"})
    if not steps:
        return None
    v = task.get("verdict") or {}
    started = task.get("started") or now
    last = max([t.get("t", started) for t in task.get("trace") or []] or [started])
    return {"id": str(task.get("id") or int(started * 1000)), "ts": started, "goal": task["goal"][:300],
            "steps": steps[-40:], "verdict": v.get("verdict"), "verdict_source": v.get("source", ""),
            "checks": [r.get("check", "") for r in v.get("results", [])][:6],
            "checks_ok": bool(v.get("results")) and all(r.get("ok") for r in v["results"]),
            "secs": round(max(0.0, last - started), 1), "brain": str(brain or task.get("model") or "")[:60],
            "skill": ran_skill, "stopped": bool(task.get("stopped")),
            "redacted": HIDDEN in json.dumps(steps, ensure_ascii=False), "jobs": jobs}


def load(now=None):
    """Episodes, oldest first (ones older than KEEP_DAYS are left out)."""
    now = now or time.time()
    with _lock:
        try:
            return db.load_episodes(since=now - KEEP_DAYS * 86400)
        except Exception:
            log_exception("learner episodes read")
            return []


def _write(eps):
    db.replace_episodes(eps[-MAX_EPISODES:])


def record(ep, now=None):
    """Add an episode, or update the one with the same id (a task can be recorded at idle time and again when the
    next request starts). What was learned from it and its jobs' results are kept. Old episodes are pruned here."""
    if not ep:
        return
    with _lock:
        eps = load(now)
        old = next((e for e in eps if e.get("id") == ep["id"]), None)
        if old:
            keep_jobs = {j["id"]: j for j in old.get("jobs", [])}
            ep = {**ep, "learned": old.get("learned", False),
                  "jobs": [keep_jobs.get(j["id"], j) if keep_jobs.get(j["id"], {}).get("status") != "working" else j
                           for j in ep["jobs"]]}
            eps = [ep if e is old else e for e in eps]
        else:
            eps.append(ep)
        _write(eps)


def job_finished(job, now=None):
    """A background job ended: note how in the episode that started it, and learn site facts from research."""
    now = now or time.time()
    with _lock:
        eps = load(now)
        for ep in reversed(eps):
            j = next((x for x in ep.get("jobs", []) if x["id"] == job.get("id") and x["status"] == "working"), None)
            if j and now - ep["ts"] < 86400:
                j.update(status=job.get("status", ""), kind=job.get("kind", ""), title=str(job.get("title", ""))[:120])
                _write(eps)
                break
    if job.get("kind") == "research" and job.get("status") == "done" and job.get("sources"):
        learn_sites(job.get("title", ""), job.get("sources") or [], job.get("cited") or [], now)


def worked(ep):
    """Strict: the Judge said done; or its checks covered only part of the goal but all passed and every step
    worked; or every background job it started finished. Stopped, failed or unchecked = no. (Either way the user
    is asked before anything becomes a skill.)"""
    if ep.get("stopped") or ep.get("verdict") is False:
        return False
    if ep.get("jobs"):
        return all(j.get("status") == "done" for j in ep["jobs"])
    if ep.get("verdict") is True:
        return True
    return bool(ep.get("checks_ok")) and all(st.get("ok") for st in ep.get("steps", []))


# ------------------------------------------------------------------ facts: which sites were good for which topic

def _words(text):
    return sorted({w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if w not in STOPWORDS and len(w) > 1})


def host(url):
    h = (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def load_facts():
    with _lock:
        try:
            d = json.loads(store.current().facts.read_text(encoding="utf-8"))
        except FileNotFoundError:
            d = {}
        except Exception:
            log_exception("learner facts read")     # a broken facts.json starts empty; the log says it happened
            d = {}
    d.setdefault("version", 1)
    d.setdefault("facts", [])
    d.setdefault("next_id", 1 + max([f.get("id", 0) for f in d["facts"]] or [0]))
    return d


def _save_facts(d):
    store.current().facts.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.current().facts.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(store.current().facts)


def learn_sites(topic, sources, cited, now=None):
    """Research on `topic` read `sources` (urls) and its report cited `cited` (urls). Count, per host, how often it
    was cited ("good") and how often it was opened and not used ("ignored")."""
    now = now or time.time()
    words = _words(topic)
    if not words:
        return None
    good = [host(u) for u in cited if host(u)]
    ignored = [host(u) for u in sources if host(u) and host(u) not in good]
    if not good and not ignored:
        return None
    with _lock:
        d = load_facts()
        f = next((x for x in d["facts"] if x.get("words") == words), None)
        if not f:
            f = {"id": d["next_id"], "topic": str(topic)[:120], "words": words, "good": {}, "ignored": {},
                 "times": 0, "confirmed": False, "learned": now}
            d["next_id"] += 1
            d["facts"].append(f)
        for h in good:
            f["good"][h] = f["good"].get(h, 0) + 1
        for h in ignored:
            f["ignored"][h] = f["ignored"].get(h, 0) + 1
        f["times"] += 1
        f["last"] = now
        d["facts"] = sorted(d["facts"], key=lambda x: x.get("last", 0))[-MAX_FACTS:]
        _save_facts(d)
        return f


def describe_fact(f):
    """'For tea: arena.com and teabox.com were used in the report; junk.com was opened and ignored.'
    An answer to a manager question (kind "answer", manager.remember) says what the user told her."""
    if f.get("kind") == "answer":
        from nova.actions import manager
        return manager.describe_answer(f)
    good = sorted(f["good"], key=lambda h: -f["good"][h])
    bad = sorted((h for h in f["ignored"] if f["ignored"][h] > f["good"].get(h, 0)), key=lambda h: -f["ignored"][h])
    parts = []
    if good:
        parts.append(" and ".join(good[:3]) + (" were" if len(good[:3]) > 1 else " was") + " used in the report")
    if bad:
        parts.append(", ".join(bad[:3]) + (" were" if len(bad[:3]) > 1 else " was") + " opened and ignored")
    return f"For {f['topic']}: " + "; ".join(parts) + (f" ({f['times']} research jobs)" if f["times"] > 1 else "") + "."


def sites_for(topic):
    """Hosts to prefer and hosts to skip for research on `topic`, from facts about a close enough topic (at least
    half of the two topics' words shared). -> (prefer, avoid, facts used)."""
    words = set(_words(topic))
    if not words:
        return [], [], []
    good, bad, used = {}, {}, []
    for f in load_facts()["facts"]:
        fw = set(f.get("words") or [])
        if not fw or len(words & fw) / len(words | fw) < 0.5:
            continue
        used.append(f)
        for h, n in f["good"].items():
            good[h] = good.get(h, 0) + n
        for h, n in f["ignored"].items():
            bad[h] = bad.get(h, 0) + n
    prefer = sorted((h for h in good if good[h] > bad.get(h, 0)), key=lambda h: -(good[h] - bad.get(h, 0)))
    avoid = sorted(h for h in bad if not good.get(h))
    return prefer, avoid, used


def rerank(links, prefer, avoid, keep=3):
    """Search results [(title, url)] -> the preferred sites first, avoided ones dropped (unless that would leave
    fewer than `keep` links, then they go last)."""
    def rank(link):
        h = host(link[1])
        for i, p in enumerate(prefer):
            if h == p or h.endswith("." + p):
                return i
        return len(prefer)
    is_bad = [any(host(u) == a or host(u).endswith("." + a) for a in avoid) for _, u in links]
    fine = [lk for lk, b in zip(links, is_bad) if not b]
    bad = [lk for lk, b in zip(links, is_bad) if b]
    out = sorted(fine, key=rank)
    if len(out) < keep:
        out += bad
    return out


def delete_fact(fid):
    with _lock:
        d = load_facts()
        before = len(d["facts"])
        d["facts"] = [f for f in d["facts"] if f["id"] != fid]
        _save_facts(d)
        return before - len(d["facts"])


def confirm_fact(fid):
    with _lock:
        d = load_facts()
        f = next((x for x in d["facts"] if x["id"] == fid), None)
        if f:
            f["confirmed"] = True
            _save_facts(d)
        return f


# ------------------------------------------------------------------ the Learner: skill offers from what worked

def _norm_words(goal):
    from nova.actions import skills
    return skills._strip_fillers(skills._norm(goal)).split()


def _replay(ep):
    """The episode's steps as clean skill steps (skills.steps_from_trace), or None."""
    from nova.actions import skills
    if ep.get("redacted"):
        return None
    steps, _ = skills.steps_from_trace([dict(s) for s in ep["steps"]], ep["goal"])
    return steps


def _shape(steps):
    return "|".join(st.get("tool") or ("brain" if "brain" in st else "report") for st in steps)


def template(ep_a, ep_b):
    """Two episodes of the same kind of task -> (steps with {input}, trigger phrase), or None. The goals must share
    their first words (the trigger, e.g. 'search for') and differ in the rest (the input, 'tea' / 'coffee'); the
    steps must be the same except one argument, which holds that input in both."""
    a, b = _replay(ep_a), _replay(ep_b)
    if not a or not b or len(a) != len(b) or _shape(a) != _shape(b):
        return None
    wa, wb = _norm_words(ep_a["goal"]), _norm_words(ep_b["goal"])
    n = 0
    while n < min(len(wa), len(wb)) and wa[n] == wb[n]:
        n += 1
    prefix, ra, rb = " ".join(wa[:n]), " ".join(wa[n:]), " ".join(wb[n:])
    if not prefix or not ra or not rb or ra == rb or len(prefix) < 3:
        return None
    diffs = []
    for i, (x, y) in enumerate(zip(a, b)):
        if "tool" not in x:
            if x != y:
                return None
            continue
        if x["tool"] != y["tool"] or set(x["args"]) != set(y["args"]):
            return None
        for k in x["args"]:
            if x["args"][k] != y["args"][k]:
                diffs.append((i, k))
    if len(diffs) != 1:
        return None
    i, k = diffs[0]
    va, vb = a[i]["args"][k], b[i]["args"][k]
    if not isinstance(va, str) or not isinstance(vb, str):
        return None
    ta = re.sub(re.escape(ra), "{input}", va, count=1, flags=re.I)
    tb = re.sub(re.escape(rb), "{input}", vb, count=1, flags=re.I)
    if "{input}" not in ta or ta.lower() != tb.lower():
        return None
    a[i]["args"][k] = ta
    return a, prefix


def _skill_name(words):
    from nova.actions import skills
    return skills.slug("-".join(words.split()[:5])) or "saved-task"


def learn(is_skill=None, now=None):
    """One idle-time pass. is_skill(goal) -> True when a skill already covers that request. Queues questions on the
    daily list (questions.py) and marks every new episode as learned. Returns the questions it queued (ids)."""
    from nova.actions import questions
    now = now or time.time()
    is_skill = is_skill or (lambda goal: False)
    queued = []
    with _lock:
        eps = load(now)
        fresh = [e for e in eps if not e.get("learned") and all(j.get("status") != "working" for j in e.get("jobs", []))]
        if not fresh:
            return queued
        for e in fresh:
            e["learned"] = True
        _write(eps)
    good = [e for e in eps if worked(e) and not e.get("skill")]
    for e in fresh:
        if not worked(e) or e.get("skill") or is_skill(e["goal"]):
            continue
        steps = _replay(e)
        if not steps:
            continue
        shape = _shape(steps)
        # the same kind of task done before with a different input -> a skill with an {input} slot
        made = None
        for other in reversed(good):
            if other is e or other["id"] == e["id"] or _norm_words(other["goal"]) == _norm_words(e["goal"]):
                continue
            made = template(other, e)
            if made:
                break
        if made:
            tsteps, prefix = made
            qid = questions.add(
                "save_skill", f"You asked me to “{prefix} …” more than once ({other['goal'][:60]} / {e['goal'][:60]}). "
                f"Shall I save it as a skill, so “{prefix} <anything>” runs the same steps straight away?",
                "it worked both times and only one word changed", key=f"slot:{shape}:{prefix}",
                data={"episode": e["id"], "steps": tsteps, "examples": [prefix], "name": _skill_name(prefix),
                      "goal": e["goal"]}, now=now)
        elif len([s for s in steps if "tool" in s]) >= 2:
            qid = questions.add(
                "save_skill", f"“{e['goal'][:80]}” worked. Shall I save it as a skill, so next time I do the same "
                "steps straight away?", f"it took {len(steps)} steps and my checks said it was done",
                key=f"one:{shape}:{' '.join(_norm_words(e['goal']))}",
                data={"episode": e["id"], "steps": steps, "examples": [" ".join(_norm_words(e["goal"]))],
                      "name": _skill_name(" ".join(_norm_words(e["goal"]))), "goal": e["goal"]}, now=now)
        else:
            continue
        if qid:
            queued.append(qid)
    return queued
