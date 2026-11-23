"""Which local brain does the job.

Nova's everyday brain is small and fast (qwen3-vl:4b-instruct). On a 6 GB laptop GPU only one 7-8B model fits at a
time, so a bigger brain is loaded only for a task that needs it (loading takes up to ~2 minutes), once per task:

  code  -> the coding model takes over the task (qwen2.5-coder:7b) until it ends.
  long  -> a plan with more than heavy_steps steps is carried out by qwen3:8b (better with tools) instead.
  deep  -> the deep-reasoning model writes a full plan WITHOUT tools (qwen3.5:4b, thinking); the everyday brain then
           carries the plan out step by step with the tools. (Setting "deep_runs": true lets a tool-capable deep
           model do the steps itself.)

A task is routed either by the words in the request (code_wanted / deep_wanted) or because the everyday brain calls
the `escalate` tool. Everything falls back to the everyday brain when a model isn't installed or fails.
Only for a local brain: a cloud brain is already strong, so nothing is routed.
"""
import re

from nova import prompts as promptfiles
from nova.brain import llm

import json

from nova import shared, store

# config/model_routing.json (in the project, so git keeps it) holds every knob below: written with these defaults when
# missing, then yours to edit by hand or in the Model dialog - changes apply on the next request, no restart.
# A key in data/settings.json overrides the file.
DEFAULTS = {
    "_help": {
        "auto_route": "false = never switch brains; everything stays on the everyday model (settings.json 'model').",
        "coder_model": "Takes over a coding task. Must be installed and able to call tools.",
        "deep_model": "Writes a full plan (no tools) for a complex task.",
        "deep_runs": "true = the deep model also carries out its own plan, but only if a test shows it really calls tools.",
        "heavy_model": "Carries out a LONG plan with the tools instead of the everyday model (stronger with tools, slower).",
        "heavy_steps": "A plan with MORE numbered steps than this goes to heavy_model. Higher = the everyday model does more.",
        "long_request_chars": "A request longer than this many characters is treated as complex and gets a plan.",
        "code_triggers": "Words/regex in the request that hand the task to coder_model. Edit freely; bad regex is ignored.",
        "deep_triggers": "Words/regex in the request that ask deep_model for a plan first.",
        "question_triggers": "A coding request with these words (and none of job_triggers) is a QUESTION: coder_model "
                             "answers it in words with no tools, so it can't wander off running commands.",
        "job_triggers": "Words that make a coding request a JOB on this laptop (run, install, edit a file...): "
                        "coder_model keeps its tools. These win over question_triggers.",
        "how_it_flows": "Everyday model gets every request. code_triggers -> coder_model for the whole task. "
                        "deep_triggers or a long request -> deep_model plans, then the everyday model (or heavy_model if "
                        "the plan is long) carries it out. The everyday model can also call the 'escalate' tool itself. "
                        "Each new request starts back on the everyday model.",
    },
    "auto_route": True,
    "coder_model": "qwen2.5-coder:7b",
    "deep_model": "qwen3.5:4b",
    "deep_runs": False,
    "heavy_model": "qwen3:8b",
    "heavy_steps": 6,
    "long_request_chars": 300,
    "code_triggers": ["code", "coding", "script", "function", "class", "python", "javascript", "typescript",
                      "powershell script", "bug", "debug", "refactor", "compile", "stack ?trace", "traceback",
                      "exception", "unit tests?", "regex", "sql", "html", "css", "api", "git (diff|merge|rebase)",
                      "fix (the |this |my )?(error|bug)"],
    "deep_triggers": ["step[- ]by[- ]step", "full plan", "make a plan", "plan (out|for|how)", "come up with a plan",
                      "strategy", "think (it|this) through", "figure out how", "work out how", "analy[sz]e",
                      "compare .+ (with|and|vs)", "research .+ and", "set ?up .+ and", "organi[sz]e .+ and",
                      "troubleshoot", "root cause", "why is .+ (slow|failing|not working)"],
    "question_triggers": ["what('?s| is) wrong", "what('?s| is) the (error|problem|issue|difference|meaning)",
                          "why (does|do|is|am|are|won'?t|doesn'?t|isn'?t|can'?t)", "what does", "what('?s| is) an?",
                          "explain", "how (do|can|should|would) (i|you|we)", "how to", "meaning of", "is it (ok|okay|bad|better)",
                          "which is better", "what('?s| is) the (best|right) way"],
    "job_triggers": ["run", "execute", "install", "uninstall", "create", "make (a|me a|the) (file|folder|script|project)",
                     "save", "open", "edit", "change", "rename", "delete", "commit", "push", "pull", "clone",
                     "deploy", "launch", "start", "set ?up", "in the terminal", "in vs ?code",
                     "on (my|this) (laptop|pc|computer)", "folder", "my (project|repo|repository|files?)",
                     "[a-z]:[\\/]", "[\\w-]+\\.(py|js|ts|ps1|bat|html|css|json|txt)", "check (my|which|what)"],
}
_cache = {"key": None, "conf": None}


def _file():
    return store.current().routing


def _load():
    """The routing file merged over the defaults. Created with the defaults when missing; a broken file is ignored
    (the defaults apply) so a typo can never stop Nova."""
    f = _file()
    try:
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(DEFAULTS, indent=2, ensure_ascii=False), encoding="utf-8")
        key = (str(f), f.stat().st_mtime_ns, f.stat().st_size)
        if _cache["key"] == key:
            return _cache["conf"]
        conf = {**DEFAULTS, **{k: v for k, v in json.loads(f.read_text(encoding="utf-8")).items() if k in DEFAULTS}}
        _cache.update(key=key, conf=conf)
        return conf
    except Exception:
        return dict(DEFAULTS)


BORROW_KEEP = "2m"      # a borrowed bigger model leaves the GPU soon after its task, so the everyday model can come back
# The planner's token budget counts its thinking too. At 900 (the old value) deepseek-r1:8b finished 1 plan of 5 and
# qwen3.5:4b none: the thinking used it all up (measured 2026-11-23). qwen3.5 needed up to ~3800, deepseek ~1600.
PLAN_PREDICT = 4000

ROUTING_KEYS = ("auto_route", "coder_model", "deep_model", "heavy_model", "heavy_steps", "deep_runs")


def save(changes):
    """Write the routing choices made in the Model dialog into the file (keeping everything else in it)."""
    conf = dict(_load())
    for k, v in (changes or {}).items():
        if k not in ROUTING_KEYS:
            continue
        if k in ("auto_route", "deep_runs"):
            conf[k] = v is True
        elif k == "heavy_steps":
            conf[k] = max(1, min(30, int(v)))
        elif isinstance(v, str) and v.strip():
            conf[k] = v.strip()
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(conf, indent=2, ensure_ascii=False), encoding="utf-8")
    _cache["key"] = None
    return conf


def _regex(words, fallback):
    ok = []
    for w in words if isinstance(words, list) else []:
        try:
            re.compile(str(w))
            ok.append(str(w))
        except re.error:
            pass
    return re.compile(r"\b(" + "|".join(ok or fallback) + r")\b", re.I)


PLAN_PROMPT = promptfiles.load("plan")


def options(s):
    """The routing settings: defaults < data/model_routing.json < data/settings.json."""
    conf = _load()
    return {k: s.get(k, conf[k]) for k in DEFAULTS if k != "_help"}


def installed(model):
    names = llm.list_models() or []
    return model in names or f"{model}:latest" in names


def code_wanted(goal):
    return bool(_regex(_load()["code_triggers"], DEFAULTS["code_triggers"]).search(goal or ""))


def deep_wanted(goal):
    c = _load()
    return bool(_regex(c["deep_triggers"], DEFAULTS["deep_triggers"]).search(goal or "")) or len(goal or "") > int(c["long_request_chars"])


def code_question(goal):
    """A coding request that only needs words ("what's wrong with print('hi' - 1)?"), not a job on this laptop."""
    c, g = _load(), goal or ""
    return (bool(_regex(c["question_triggers"], DEFAULTS["question_triggers"]).search(g))
            and not _regex(c["job_triggers"], DEFAULTS["job_triggers"]).search(g))


def pick_code_model(s):
    """The model that takes over a coding task, or None (not installed / not able to call tools)."""
    m = options(s)["coder_model"]
    return m if m != s["model"] and installed(m) and llm.supports_tools(m) else None


def make_plan(s, goal, on_think=None):
    """Full plan from the deep model (no tools). Returns (plan_text, model) or (None, reason)."""
    m = options(s)["deep_model"]
    if not installed(m):
        return None, f"{m} isn't installed"
    try:
        from nova.brain import toolpick
        names = ", ".join(toolpick.pick(goal)[:40])
    except Exception:
        names = "any of Nova's tools"
    try:
        _, text, _ = llm.chat(m, [{"role": "user", "content": PLAN_PROMPT.format(goal=goal[:1500], tools=names,
                                                                                    folders=shared.folders_line())}], [],
                              think=True, num_predict=PLAN_PREDICT, on_think=on_think, keep_alive=BORROW_KEEP,
                              own_sampling=True)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    text = (text or "").strip()
    return (text, m) if text else (None, f"{m} gave no plan")


def step_count(plan):
    """How many numbered steps the plan has."""
    return len(re.findall(r"^\s*\d+[.)]\s", plan or "", re.M))


def heavy_executor(s, plan):
    """A long plan (more than heavy_steps steps) is carried out by the stronger tool-user (qwen3:8b) instead of the
    small everyday brain. None when the plan is short, or that model isn't installed / can't call tools."""
    o = options(s)
    m = o["heavy_model"]
    if step_count(plan) <= int(o["heavy_steps"]) or m == s["model"]:
        return None
    return m if installed(m) and llm.supports_tools(m) else None


def deep_can_run(s):
    """The deep model executes its own plan only when the user opted in ("deep_runs") AND it really calls tools.
    Ollama's capability list is not enough: deepseek-r1:8b is labelled "tools" but answers in words instead of
    calling one (tested). So it is probed once with a harmless dummy tool, and the answer is remembered."""
    o = options(s)
    if not o["deep_runs"]:
        return False
    m = o["deep_model"]
    if m not in _probe:
        _probe[m] = _calls_tools(m)
    return _probe[m]


_probe = {}
_PROBE_TOOL = [{"name": "open_app", "description": "Open an app on the laptop.",
                "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}]


def _calls_tools(model):
    try:
        if not llm.supports_tools(model):
            return False
        _, _, calls = llm.chat(model, [{"role": "user", "content": "Open Notepad."}], _PROBE_TOOL, num_predict=200, keep_alive=BORROW_KEEP)
        return bool(calls)
    except Exception:
        return False
