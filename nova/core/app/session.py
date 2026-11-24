"""The conversation's state (messages, pending approval, the current task) and her live thoughts."""


import json
import re
import threading
import time

from nova import platform
from nova import runtime
from nova import intents
from nova.avatar import avatar_client
from nova.actions import knowledge
from nova.actions import mcp
from nova.actions import skills
from nova.actions import registry
from nova.actions import recorder
from nova.actions import workers
from nova.actions import tools
from nova.actions import shell_policy
from nova.actions import risk
from nova.core.app import eventbus


state = {"messages": [], "pending": None}


lock = eventbus.BusyLock()    # held while she handles a request; taking and letting go are published as "busy"


recorder.busy = lock.locked     # 👀 while she handles a request, the windows she opens aren't the user's steps


def _windows_display_name():
    """The first name on the Windows account, e.g. 'Vikrant' - a starting point for the user's profile."""
    name = platform.current().user_display_name()
    return name.split()[0] if name else ""


knowledge.seed(_windows_display_name())


# What the activity line says a tool did, and the Allow / No question for it: both come from each tool's
# @tool declaration (nova/actions/registry.py) through _labels below.
FRIENDLY = {}


ASK = {}


def _labels(spec):
    """A tool declared with @registry.tool brings its own labels (nova/actions/registry.py)."""
    FRIENDLY[spec.name] = spec.friendly
    if spec.ask:
        ASK[spec.name] = spec.ask


registry.on_register(_labels)


def ask_label(name):
    """What the Allow / No question calls this action."""
    return mcp.label(name) if mcp.owns(name) else ASK.get(name, name)


def ask_text(c):
    """The Allow / No question for one call: a skill is named, so "Run a skill" doesn't hide which one."""
    if c["name"] == "run_skill":
        s = skills.get((c["args"] or {}).get("name", ""))
        if s:
            first = re.split(r"(?<=[.!?])\s", s["description"] or "")[0].rstrip(".")[:90]
            return f"Run my '{s['name']}' skill" + (f" ({first})" if first else "")
    if c["name"] in ("start_coding_job", "retry_coding_job") and coding_job_worker(c) == "claude":
        budget = float(workers.config()["claude"].get("budget_usd") or 1)
        return f"Send this coding job to Claude Code (paid, up to ${budget:.2f})"
    return ask_label(c["name"])


def coding_job_worker(call):
    args = call.get("args") or {}
    if call.get("name") == "retry_coding_job":
        return workers.retry_target(args.get("which") or "latest", args.get("worker") or "")[1]
    return workers.pick_worker(args.get("worker") or "auto", args.get("size") or "small")


def skill_needs_ok(args):
    """A skill that changes things asks first unless the request said it outright. A fuzzy match ("sounds like"), or a
    skill the brain picked on its own, is only a guess; one marked explicit_only (pushing code...) always needs the
    user's own words."""
    s = skills.get(args.get("name", ""))
    if not s:
        return False
    task = state.get("task") or {}
    how = task.get("skill_how") if task.get("skill") == s["name"] else ""
    if s["invoke"] == "explicit_only":
        return how != "exact"
    if s["risk"] == "read_only" or s["invoke"] == "auto":
        return False
    return how != "exact"


def skill_fix_note(text):
    """Right after a skill ran, a correction ("no, also unmute") is about that skill: tell the brain how to fix it."""
    last = state.get("last_skill")
    if not last or time.time() - last["ts"] > 180 or not intents.CORRECTION.search(text or ""):
        return ""
    s = skills.get(last["name"])
    if not s or s["kind"] != "routine":
        return ""
    skills.stats_update(s["name"], lambda d: d.__setitem__("corrections", d.get("corrections", 0) + 1))
    steps = "; ".join(f"{st['id']}: {skills.render_step(st)[:60]}" for st in skills._ids_for([dict(x) for x in s["steps"]]))
    return (f"\n\nThe user may be correcting your skill '{s['name']}' (its last run {last['result']}). Its steps: {steps}. "
            "If they say how it should work, do what they asked now, then change the skill with edit_skill using small "
            "operations (insert_step, replace_step, delete_step...). Don't retype the whole skill.")


def is_risky(call):
    """Does this call need the user's OK under the current autonomy level?"""
    name, args = call["name"], call["args"]
    s = config.settings()
    if shell_policy.needs_ok(name, args):               # a shell command that changes things: always ask, at every
        return True                                     # autonomy level, no opt-out (decided 2026-11-21, roadmap R2)
    if name == "power_action" and args.get("action") == "cancel":
        return False
    if name in ("press_keys", "type_text") and conversation.wrong_app(call):
        return False                                    # it will be refused (wrong window): nothing to ask about
    # Coding workers (workers.py, manager plan section 9 answer 3): the cheap one runs without asking, Claude Code
    # costs money so it always asks, and nothing is committed without the user's OK - whatever the autonomy level.
    if name == "commit_coding_job":
        return True
    if name in ("start_coding_job", "retry_coding_job"):
        return coding_job_worker(call) == "claude" or bool(state.get("remote"))
    if name == "run_skill" and skill_needs_ok(args):    # it only sounds like that skill (or the brain picked it): ask
        return True
    if risk.always_asks(name, args, s):                 # 4e: level 2 (delete, send, pay, install, sign in, power…)
        return True                                     # asks every time, at every autonomy level
    sk = skills.get(args.get("name", "")) if name == "run_skill" else None
    if sk and sk["kind"] == "routine" and any(risk.always_asks(st["tool"], st.get("args"), s)
                                              for st in sk["steps"] if "tool" in st):
        return True                                     # ...and so does a routine with such a step
    if mcp.owns(name):                                  # 🔌 connector tools (mcp.py): ask unless known to be safe
        if mcp.is_safe(name):
            return False
        return s["autonomy"] != "full" or bool(state.get("remote"))
    if state.get("remote"):                             # 📱 from the phone: the user can't see the screen, so always ask
        return name in tools.RISKY or tools.is_dangerous(name, args) or (
            name == "run_skill" and bool(skills.get(args.get("name", ""))))
    if name == "power_action" and s.get("power_ok"):    # ⚙ "Shut down without asking": the user said yes up front
        return False
    mode = s["autonomy"]
    if mode == "full":
        return False
    if name == "run_skill":     # a routine asks once, up front, if any of its steps would ask on its own
        s = skills.get(args.get("name", ""))
        if not (s and s["kind"] == "routine"):
            return False
        text = str(args.get("input") or "")
        calls = [{"name": st["tool"], "args": skills.fill(st["args"], text)} for st in s["steps"] if "tool" in st]
        for st in s["steps"]:             # a "brain" step (or an on_fail brain) may use these tools: ask for those too
            if "brain" in st or any(a["do"] == "brain" for a in (st.get("on_fail") or [])):
                calls += [{"name": t, "args": {}} for t in (st.get("tools") or skillrun.SKILL_BRAIN_TOOLS)]
        return any(is_risky(c) for c in calls)
    if mode == "trusted":
        return tools.is_dangerous(name, args)
    return name in tools.RISKY


MAX_THOUGHTS = 300


thoughts = {"next": 1, "turn": 0, "list": []}


_thought_lock = threading.Lock()


_cloud_q = []                           # the newest thought waiting for 3D Nova's cloud


_cloud_wake = threading.Event()


def think(text, cloud_too=True):
    """Note one thought. cloud_too: also show it in the thought cloud over 3D Nova (plain results stay in the log)."""
    text = " ".join(str(text or "").split())
    if not text:
        return
    text = text if len(text) <= 300 else text[:297] + "…"
    with _thought_lock:
        t = {"id": thoughts["next"], "turn": thoughts["turn"], "t": round(time.time(), 2), "text": text}
        thoughts["list"].append(t)
        thoughts["next"] += 1
        eventbus.publish("thought", t)          # inside the lock, so the pages get them in order
        del thoughts["list"][:-MAX_THOUGHTS]
    conversation.task_log(f"THINK: {text}")
    if cloud_too:
        _cloud_q[:] = [text]
        _cloud_wake.set()


def thoughts_done():
    """The answer is ready: the thought cloud goes away."""
    _cloud_q[:] = [""]
    _cloud_wake.set()


def _cloud_worker():
    """Sends only the newest thought to 3D Nova, at most one every ~1.5 s, so each one stays up long enough to read
    and a fast run of steps never queues up behind her."""
    while True:
        _cloud_wake.wait()
        _cloud_wake.clear()
        if not _cloud_q:
            continue
        text = _cloud_q.pop()
        avatar_client.send("thought", text=text[:140], secs=12 if text else 0, timeout=2)
        if text:
            time.sleep(1.5)


runtime.spawn("session.cloud_worker", _cloud_worker)


class ThoughtStream:
    """A thinking model's reasoning, cut into sentences as it is written - one log line each."""

    END = re.compile(r"(?<=[.!?])\s+|\n+")
    LIMIT = 40                           # a long ramble: the first lines say enough, the task log has the rest

    def __init__(self):
        self.buf, self.n = "", 0

    def feed(self, piece):
        self.buf += piece
        while True:
            m = self.END.search(self.buf)
            if not m:
                return
            line, self.buf = self.buf[:m.start()].strip(), self.buf[m.end():]
            self._note(line)

    def flush(self):
        self._note(self.buf.strip())
        self.buf = ""

    def _note(self, line):
        if len(line) < 3:
            return
        self.n += 1
        if self.n <= self.LIMIT:
            think("🧠 " + line)
        elif self.n == self.LIMIT + 1:
            think("🧠 …(still reasoning)", cloud_too=False)


def _short_args(args):
    """A tool call's arguments in a few words, for a thought line."""
    if not isinstance(args, dict) or not args:
        return ""
    parts = []
    for k, v in args.items():
        v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        parts.append(f"{k}: {v[:60] + '…' if len(v) > 60 else v}")
    s = ", ".join(parts)
    return s[:140] + "…" if len(s) > 140 else s


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import config, conversation, skillrun  # noqa: E402
