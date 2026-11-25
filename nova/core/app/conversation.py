"""One request, start to finish: the Stop switch, tasks and routing, history, running tool calls and the agent loop."""


import json
import re
import threading
import time

from nova import runtime
from nova import prompts as promptfiles
from nova import store
from nova.avatar import avatar_client
from nova.brain import cloud, llm, retrieval, router, toolpick
from nova.core import characters
from nova.actions import knowledge
from nova.actions import mcp
from nova.actions import skills
from nova.actions import judge
from nova.actions import recorder
from nova.actions import tools
from nova import shared
from nova import intents
from nova import protocol, task_state
from nova import log
from nova.log import log_exception


ACT_TOOLS = {"open_app", "open_website", "web_research", "open_path", "window_action", "minimize_all_windows", "type_text",
             "close_all_windows", "run_in_terminal", "press_keys", "fill_in", "click_element", "click_nth", "click_at", "scroll", "video_control", "drag_window"}


SLOW_TOOLS = {"open_app", "open_website", "web_research", "open_path"}   # these need a moment before the screen settles


LOOK_FROM_STEP = 2      # one-step requests ("open Chrome") stay quick: only the window list is checked


STEP_CHECK_WAIT, FINAL_CHECK_WAIT = 1.5, 5.0   # seconds a judge check may wait to come true: after a step / at the end


MAX_TURNS, MAX_TASK_TURNS = 10, 24
TASK_SECS = 600             # 4c: a task stops after 10 minutes too (Continue picks it up from the same step)


# A cloud brain on High/Max reasoning plans its steps well, so a big job (find my GitHub username, then set up the
# repo...) gets more steps before she pauses; 24 is a guard against a small model going round in circles.
DEEP_TASK_TURNS = 50


CHECK_PROMPT = promptfiles.load("check")


def task_log(line):
    log.event("log", line)           # the row code can query; task_log.txt below is the copy people read
    try:
        if store.current().task_log.exists() and store.current().task_log.stat().st_size > 300_000:
            store.current().task_log.write_text(store.current().task_log.read_text(encoding="utf-8")[-100_000:], encoding="utf-8")
        with store.current().task_log.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
    except Exception:
        pass        # intentional: this is the log itself; a full disk must not break the turn


llm.log = task_log                      # the brain notes oddities (a call written as text) in the same log


class Stopped(BaseException):
    """The user pressed Stop. A BaseException, so the many `except Exception` fallbacks can't swallow it."""


stop_flag = threading.Event()


STOPPED_REPLY = "Okay, I stopped."


def check_stop():
    if stop_flag.is_set():
        raise Stopped()


def interruptible(fn, *args, **kwargs):
    """Run a slow call so that Stop can cut the wait short. The call itself is left to finish in the background and
    its result is thrown away (an HTTP request to the brain can't be pulled back once it's sent)."""
    check_stop()
    box, done = {}, threading.Event()

    def run():
        try:
            box["v"] = fn(*args, **kwargs)
        except BaseException as e:
            box["e"] = e
        finally:
            done.set()
    runtime.spawn("conversation.run", run)
    while not done.wait(0.15):
        check_stop()
    check_stop()
    if "e" in box:
        raise box["e"]
    return box["v"]


def unless_stopped(fn):
    """A streaming callback that goes quiet once Stop is pressed (the abandoned brain call keeps writing)."""
    return None if fn is None else (lambda piece: None if stop_flag.is_set() else fn(piece))


def stop_now():
    """Stop whatever Nova is doing: the task, her voice, the thought cloud."""
    busy = session.lock.locked()
    if busy:
        stop_flag.set()
    voice_io.mouth.stop()
    if busy:
        session.think("🛑 You pressed Stop - dropping everything.", cloud_too=False)
    session.thoughts_done()
    task_log(f"STOP pressed ({'while working' if busy else 'while idle'})")
    return busy


def new_turn():
    """Thoughts from here on belong to a new request (the chat groups them per request)."""
    with session._thought_lock:
        session.thoughts["turn"] += 1


def new_task(goal):
    """Start a request. Returns a note to add to the user's message (only when a paused task is resumed)."""
    task_log(f"\n=== {time.strftime('%Y-%m-%d')} REQUEST: {goal[:300]}")
    new_turn()
    session.state.pop("react_after", None)
    session.state.pop("skill_final", None)
    session.state.pop("skill_pause", None)
    clean = config._unspoken(goal)[:300]
    last = session.state.get("task") or {}
    if any(x.get("ok") and x["tool"] not in skills.SKIP_IN_TRACE for x in last.get("trace", [])):   # for "remember that"
        session.state["last_trace"] = {"goal": last.get("goal", ""), "trace": list(last["trace"])}
    if not last.get("paused"):
        learning.record_episode(last, clean)         # the last task is over: one line in the episode log (learner.py)
    resumed = resume.take()                           # "continue" picked a stopped task (resume.direct checked it)
    if resumed:
        task_log(f"RESUMING task: {resumed['card']['goal'][:200]}")
        session.think(f"▶️ Carrying on with: {resumed['card']['goal'][:120]}", cloud_too=False)
    if resumed and task_state.of(last) is resumed["card"]:      # the task still in memory: carry on with it as it is
        last["paused"] = False
        return resume.note(resumed)
    resume.leave(last)                                # one laptop task at a time: the last one stays stopped
    clean = resumed["card"]["goal"] if resumed else clean
    skill = how = None
    try:
        skill, how = skills.pick(clean)       # exact (it said the skill), fuzzy (it only sounds like it) or neither
        task_log(skills.last_decision)
        keep = (list(last.get("used", ())) + (skill["tools"] if skill and how == "exact" else [])
                + (["run_skill"] if skills.load() else []))       # her skill names are in the prompt, so is run_skill
        picked = toolpick.pick(clean, keep=keep)
        named = learning.watch_note()                 # right after "watch me ... done": the next words are probably its name
        if named:
            keep.append("save_task_as_skill")
        picked = toolpick.pick(clean, keep=keep) if named else picked
        facts = (knowledge.prompt_block(clean) + skills.prompt_block(clean, skill, how or "exact") + session.skill_fix_note(clean)
                 + named)
        if skill:
            session.think(f"📜 This sounds like my '{skill['name']}' skill." if how == "exact"
                  else f"📜 This might be my '{skill['name']}' skill - I'll check with you first.", cloud_too=False)
    except Exception as e:      # never let picking stop her answering: all tools, the plain summary
        task_log(f"PICK failed: {type(e).__name__}: {e}")
        skill = how = None
        picked, facts = None, knowledge.prompt_block()
    session.state["task"] = {"id": f"{time.time():.3f}", "started": time.time(),
                     "goal": clean, "start": len(session.state["messages"]), "steps": 0, "did": [], "done": None,
                     "screen": "", "nudged": False, "tools": picked, "facts": facts, "used": [],
                     "skill": skill["name"] if skill else "", "skill_how": how or "",
                     "app": tools.named_app(clean)}      # "type hello in notepad": keys and typing go only there
    if resumed:
        session.state["task"]["card"] = resumed["card"]
    else:
        task_state.attach(session.state["task"], *((task_state.steps_from_skill(skill), "skill") if skill and how == "exact" else ()))
    direct = skillrun.plan_direct(session.state["task"], skill, how)     # an exact routine match: no brain turn first
    try:
        made = judge.goal_checks(clean)   # the Judge's checks for this goal (more may be proposed later)
    except Exception as e:
        task_log(f"JUDGE goal checks failed: {type(e).__name__}: {e}")
        made = {"checks": [], "covers": False}
    session.state["task"].update(checks=made["checks"], covers=made["covers"], proposed=bool(made["checks"]), strict=True,
                         looks=0, verdict=None)
    if made["checks"]:
        task_log("JUDGE CHECKS (code): " + "; ".join(judge.describe(c) for c in made["checks"])
                 + (" - cover the whole goal" if made["covers"] else " - cover part of it"))
    s = config.settings()
    if cloud.parse(config.brain(s)) and llm.cloud_resting():   # the cloud failed a moment ago: this whole task runs locally
        session.state["task"]["model"] = s["model"]
        task_log(f"CLOUD RESTING -> local {s['model']} for this task")
    if cloud.parse(config.brain(s)) and not s.get("email_cloud") and not session.state["task"].get("model") and intents.EMAIL.search(clean):
        session.state["task"]["model"] = s["model"]      # her mail never goes to a cloud brain unless ⚙ allows it
        task_log(f"EMAIL: local {s['model']} for this task")
    if not cloud.parse(task_brain(s)) and not direct:      # no brain turn coming: no helper brain to load
        choose_local_route(session.state["task"], s)
    task_log(f"PICKED {len(picked) if picked else 'all'} tools ({retrieval.status()}): "
             + (", ".join(n for n in picked if n not in toolpick.CORE) if picked else "-"))
    return resume.note(resumed) if resumed else ""


def task_brain(s, task=None):
    """The brain this task is really on: the one it was switched to (local fallback, coder, deep...) or the chosen one."""
    task = task if task is not None else session.state.get("task")
    return (task or {}).get("model") or config.brain(s)


def choose_local_route(task, s):
    """Pick the local helper brain from the request's words (brain/router.py); _agent_loop acts on it next turn."""
    if task and router.options(s)["auto_route"] and not task.get("routed"):
        goal = task.get("goal", "")
        if intents.JOB_STATUS.search(goal):      # "how's the coding job going?" is a background_jobs lookup, not code
            task["route"] = None            # (live 2026-11-21: the coder brain took 269 s and guessed "still running")
            return
        task["route"] = "code" if router.code_wanted(goal) else "deep" if router.deep_wanted(goal) else None


def local_route_for(task, s):
    """The local helper brain this task would get if it ran locally ("code" / "deep" / None), without choosing it."""
    if not task or task.get("routed"):
        return None
    probe = {"goal": task.get("goal", "")}
    choose_local_route(probe, s)
    return probe.get("route")


# Live test: asked what's wrong with print('hi' - 1), the coder ran `python3` (on Windows that opens the Microsoft
# Store), was told "Python was not found" and said to install Python - which was already there.
CODER_NOTE = "\n\n" + promptfiles.load("coder_note")


CODER_QUESTION_NOTE = ("\n\nTHIS REQUEST IS A QUESTION. You have no tools for it: answer now in plain words - what is "
                       "wrong and how to fix it, with a few lines of corrected code if that helps. Keep it short.")


def route_task(kind, why=""):
    """Give this task a bigger local brain (brain/router.py). Returns the text the everyday brain is told."""
    task, s = session.state.get("task"), config.settings()
    if not task or cloud.parse(task_brain(s, task)):
        return "No other brain to switch to - carry on yourself."
    if kind == "cloud":
        return brains.CLOUD_TAKES_OVER if brains.climb(task, why or "it asked for help") else "No cloud brain to switch to - carry on yourself."
    if kind == "code":
        m = router.pick_code_model(s)
        if not m:
            return "The coding brain isn't available - do the coding yourself, carefully."
        task["model"] = m
        task["facts"] = task.get("facts", "") + CODER_NOTE    # in her instructions: route_task's reply isn't always shown
        if router.code_question(task["goal"]):                # a question, not a job here: words only, no tools
            task["answer_only"] = True
            task["facts"] += CODER_QUESTION_NOTE
            task_log("ROUTE code question -> answer in words, no tools")
        session.think(f"🔀 Code job - switching to my coding brain ({m}). Loading it can take a minute…")
        task_log(f"ROUTE code -> {m} ({why[:100]})")
        return f"You are now the coding brain ({m}). Carry on with the task: {task['goal']}"
    if task.get("plan"):
        return "You already have the plan (see your instructions). Follow it."
    session.think("🧠 Complex job - asking my deep-thinking brain to plan it first. Loading it can take a minute…")
    t0 = time.time()
    ts = session.ThoughtStream()
    plan, m = interruptible(router.make_plan, s, task_state.plan_goal(task), on_think=unless_stopped(ts.feed))
    ts.flush()
    if not plan:
        task_log(f"ROUTE deep failed: {m}")
        if brains.climb(task, f"my local planner couldn't write a plan ({m})"):
            return brains.CLOUD_TAKES_OVER
        session.think(f"🧠 Couldn't get a plan ({m}) - I'll work it out myself.", cloud_too=False)
        return f"No plan available ({m}). Work it out yourself, one step at a time."
    task_log(f"ROUTE deep -> plan from {m} in {time.time() - t0:.0f}s:\n      " + plan.replace("\n", "\n      "))
    task["plan"] = plan
    task_state.set_plan(task_state.of(task), task_state.steps_from_plan(plan, {t["name"] for t in tools.TOOLS}), "deep brain")
    task["facts"] = (task.get("facts", "") + "\n\nPLAN for this task, written by your deep-thinking brain - "
                     "follow it step by step:\n" + plan + "\nThe plan is advice from another brain: it may name tools "
                     "you don't have or add silly or unsafe steps. Use only your real tools, skip steps that don't "
                     "help, never delete or change anything the user hasn't agreed to, and stop when the goal is reached.")
    if router.deep_can_run(s):
        task["model"] = m
        session.think(f"🧠 Plan ready ({time.time() - t0:.0f}s) - the deep brain carries it out.")
        return f"You are now the deep brain ({m}). Follow the plan in your instructions."
    heavy = router.heavy_executor(s, plan)
    if heavy:
        task["model"] = heavy
        session.think(f"🧠 Plan ready ({time.time() - t0:.0f}s), {router.step_count(plan)} steps - that's a big job, so my "
              f"stronger tool brain ({heavy}) carries it out.")
        task_log(f"ROUTE long plan ({router.step_count(plan)} steps) -> {heavy}")
        return f"You are now the stronger brain ({heavy}). Follow the plan in your instructions step by step with your tools."
    session.think(f"🧠 Plan ready ({time.time() - t0:.0f}s) - now I'll carry it out step by step.")
    return "Your deep-thinking brain wrote a plan for this task (it is in your instructions). Follow it step by step with your tools."


def more_tools(task, need):
    """She asked for abilities she doesn't see: add the closest tools for the rest of this request."""
    if not task or not task.get("tools"):
        return "All your tools are already available."
    added, task["tools"] = toolpick.add(task["tools"], need)
    task_log(f"MORE TOOLS for '{need[:80]}': {', '.join(added) or 'none'}")
    if not added:
        return "No other tool matches that. Use run_powershell if it can be done with a command, or tell the user."
    return "You now also have these tools: " + ", ".join(added) + ". Use the right one now."


def wrong_app(c):
    """The request named an app ("type hello in notepad"): refuse keys/typing that would land in another window
    (2026-11-20 she pressed ctrl+n in VS Code and typed there, because Notepad wasn't open). None when it's fine."""
    task = session.state.get("task") or {}
    app_name = task.get("app")
    if not app_name:
        return None
    try:   # right after open_app the window can take a moment to appear
        return tools.app_guard(c["name"], c.get("args"), app_name, wait=3.0 if "open_app" in task.get("used", ()) else 0.0)
    except Exception as e:
        task_log(f"APP GUARD failed: {type(e).__name__}: {e}")
        return None


def not_open_note(text):
    """For the [open windows] line: "notepad is not open" when the request names an app with no window yet,
    so the small brain calls open_app first instead of pressing new-window keys in some other app."""
    try:
        name = tools.named_app(config._unspoken(text))
        if name and not tools._app_window(name):
            return f". {name} is not open: call open_app(name='{name}') first"
    except Exception:
        log_exception("not-open note")      # the hint is optional; the request still goes ahead without it
    return ""


def _execute_one(c, approved, activity, model):
    check_stop()
    if c["name"] == "web_research" and not intents.WATCH_SEARCH.search(config._unspoken(((session.state.get("task") or {}).get("goal")) or "")):
        # The small brain picks the on-screen Google tool for plain "research X" and then makes up the answer
        # (it never reads a page). Plain research belongs in the background job, which really reads sources.
        q = (c.get("args") or {}).get("query") or ""
        c = {**c, "name": "research_in_background", "args": {"topic": q}}
        session.state["ran_as"] = c                  # traced as what ran, so a skill saved from it does the same
    if session.is_risky(c) and not approved:
        out = "The user declined this action. Do not do it."
        (session.state.get("task") or {})["declined"] = True     # her "okay, I'll wait" isn't a promise to push on
        activity.append(f"Skipped: {session.FRIENDLY.get(c['name'], session.ask_label(c['name']))}")
        session.think(f"🚫 Skipping: {session.ask_label(c['name']).lower()} (not allowed).")
    elif mcp.owns(c["name"]):                   # 🔌 a connector's tool runs in its MCP server
        session.think(f"🔌 Using the {mcp.server_of(c['name'])} connector: {c['name']}" + (
            f" ({session._short_args(c['args'])})" if session._short_args(c["args"]) else ""))
        out = mcp.call(c["name"], c["args"] or {})
        session.think("→ " + (out.strip().splitlines() or [""])[0][:160], cloud_too=False)
        activity.append(mcp.label(c["name"]))
        if session.state.get("task") is not None and c["name"] not in session.state["task"].setdefault("used", []):
            session.state["task"]["used"].append(c["name"])
    elif c["name"] == "run_skill":
        out = skillrun.run_skill(c["args"] or {}, approved, activity, model)
    elif c["name"] == "save_task_as_skill":
        out = skillrun.save_task_as_skill_now(c["args"] or {})
        activity.append("Saved a skill")
    elif c["name"] == "start_watching":
        out = learning.start_watching_now(str((c["args"] or {}).get("goal", "")))
        activity.append("Started watching")
    elif c["name"] == "stop_watching":
        out = learning.finish_watching() if recorder.recording() else "I wasn't watching."
        activity.append("Watched you")
    elif c["name"] == "react" and avatar_client.running():
        out = body.queue_reaction(c["args"] or {}, activity)
    elif c["name"] == "new_move" and avatar_client.running():
        out = body.queue_new_move(c["args"] or {}, activity)
    elif c["name"] == "research_in_background" and not learning._asked_to_research(session.state.get("task")):
        # 2026-11-18: she sent "explain how airplanes fly" and "tell me fun facts" to web research - 4 Chrome jobs piled
        # up, replies took 40-76 s and she kept interrupting with "research done". Plain questions are answered at once.
        out = ("Not run - the user did not ask you to research or look anything up. Answer from what you know "
               "in a few sentences now. If you're not sure of something, say so or ask them, don't guess.")
        session.think("🙅 That's a plain question - I'll just answer instead of researching.", cloud_too=False)
    elif c["name"] == "go_to_sleep":
        session.state["sleep_after"] = True         # she says goodnight first; heard()/chat() then really end the conversation
        activity.append(session.FRIENDLY["go_to_sleep"])
        session.think("😴 You want me to sleep - I'll nap right after this answer.")
        out = "Done: you'll go to sleep right after this answer. Just say a very short goodnight (a few words)."
    elif c["name"] == "escalate":
        a = c["args"] or {}
        out = route_task(a.get("kind") if a.get("kind") in ("code", "cloud") else "deep", str(a.get("why", "")))
        activity.append("Switched brain")
    elif c["name"] == "more_tools":
        need = str((c["args"] or {}).get("need", ""))
        session.think(f"🧰 None of my tools fit - looking for one that can {need[:80]}")
        out = more_tools(session.state.get("task"), need)
        activity.append(session.FRIENDLY["more_tools"])
        session.think("🧰 " + out.split(". Use")[0], cloud_too=False)
    elif c["name"] in ("press_keys", "type_text") and (wrong := wrong_app(c)):
        out = wrong
        activity.append(f"Skipped: {session.FRIENDLY.get(c['name'], c['name'])}")
        session.think(f"🙅 That would go into the wrong window - it has to be {session.state['task']['app']}.", cloud_too=False)
    else:
        hint = session._short_args(c["args"])
        session.think(f"🔧 Using {c['name']}" + (f" ({hint})" if hint else ""))
        tools.LAST_IMAGE["jpg_b64"] = None
        out = tools.run_tool(c["name"], c["args"])
        session.think("→ " + out.strip().split("\n")[0][:160], cloud_too=False)
        activity.append(session.FRIENDLY.get(c["name"], c["name"]))
        if session.state.get("task") is not None and c["name"] not in session.state["task"].setdefault("used", []):
            session.state["task"]["used"].append(c["name"])     # kept for the next request ("now close it")
        if tools.LAST_IMAGE["jpg_b64"]:  # show the user exactly what Nova looked at
            session.state.setdefault("images", []).append(tools.LAST_IMAGE["jpg_b64"])
            tools.LAST_IMAGE["jpg_b64"] = None
        if c["name"] in ACT_TOOLS:
            out = judging.check_step(c, out, activity, model)
    return out


def _is_real_user(m):
    return m["role"] == "user" and not protocol.is_meta_line(m["content"])


def history_limit(model=None):
    """How many stored messages to send to this model.

    Local models have an 8K window and ~5300 tokens of prompt + tools, so they keep the measured 12.
    A cloud model's window is far larger; it gets more history (up to CLOUD_HISTORY_MAX) because its
    older turns are usable, but not unbounded: prompt length is money and latency.
    """
    if not cloud.parse(model):
        return config.MAX_HISTORY
    free = max(0, llm.context_size(model) - config.PROMPT_TOKENS)
    return max(config.MAX_HISTORY, min(config.CLOUD_HISTORY_MAX, free // 1200))


def trimmed_history(model=None):
    """Last messages only, starting at a real user message so tool results keep their calls.
    During a long task the request itself must never scroll away, so it is kept in front of the latest steps."""
    limit = history_limit(model)
    msgs = session.state["messages"]
    task = session.state.get("task")
    cut = max(0, len(msgs) - limit)
    if task and task["start"] < len(msgs) and cut > task["start"]:
        tail = msgs[-(limit - 2):]
        while tail and (tail[0]["role"] == "tool" or tail[0]["content"].startswith(protocol.TOOL_RESULT_START)):
            tail = tail[1:]   # a result whose call was cut off
        return [msgs[task["start"]]] + tail
    h = msgs[cut:]
    while h and not _is_real_user(h[0]):
        h = h[1:]
    return h


def no_self_greeting(text, name=None):
    """'Hey Lucy! What's your vibe?' -> 'Hey! What's your vibe?': the small brain greets the user with HER OWN name
    (seen 2 in 12 answers for Lucy and Nova, even when the prompt says not to)."""
    name = name or characters.get(config.settings().get("character"))["name"]
    return re.sub(rf"^(\s*(?:hey|hi|hello|hiya|good (?:morning|afternoon|evening)|morning|evening))[ \t,]+{re.escape(name)}\b",
                  r"\1", text or "", flags=re.I)


class SpeechStream:
    """Collects the reply as the model writes it and sends each finished sentence to Nova's voice straight away."""

    END = re.compile(r"(?<=[.!?।])\s+|\n+")
    CLOSED_STAGE = re.compile(shared.REACT_MARKER_RE.pattern + r"|\*\(.*?\)\*|\(\*.*?\*\)", re.I)
    OPEN_STAGE = re.compile(r"\[[^\]\n]*$|\*\((?:(?!\)\*)[^\n])*$|(?:^|\n)[ \t]*([*_])(?![*_\s])[^*_\n]*$")

    def __init__(self):
        self.buf = ""
        self.cut = False
        self.opened = False      # the start of the reply has been checked for a greeting with her own name

    def feed(self, piece):
        if self.cut:                                              # everything after the cut is the echo: drop it
            return
        self.buf += piece
        if not self.opened:      # "Hey Lucy! ..." from Lucy: wait until the greeting is whole, then drop her name
            if len(self.buf) < 24 and not self.END.search(self.buf):
                return
            self.buf, self.opened = no_self_greeting(self.buf), True
        if protocol.WINDOW_NOTE_START in self.buf:                 # she started echoing the hidden window list
            self.buf = protocol.INFO_ECHO_RE.sub("", self.buf)
            if protocol.WINDOW_NOTE_START in self.buf:
                return                                            # wait for the closing bracket
        m = protocol.STARTED_MARKER_RE.search(self.buf)
        if m:                                                     # junk the model adds after its answer (it repeats the
            self.buf, self.cut = self.buf[:m.start()], True       # request as "[spoken] ..."): stop there
        elif protocol.PARTIAL_MARKER_RE.search(self.buf):
            return                                                # a half-written marker: wait for the rest
        if self.OPEN_STAGE.search(self.buf):
            return              # a half-written '[react: ...]' / '*(...)*' / '*italic line*': wait, so it isn't said
        self.buf = self.CLOSED_STAGE.sub(" ", self.buf)   # whole now: drop it before it's cut into sentences
        while True:
            m = self.END.search(self.buf)
            if not m:
                return
            sentence, self.buf = self.buf[:m.start()], self.buf[m.end():]
            voice_io.speak(sentence.strip())

    def flush(self):
        if not self.opened:
            self.buf, self.opened = no_self_greeting(self.buf), True
        self.buf = protocol.PARTIAL_MARKER_RE.sub("", self.buf)
        voice_io.speak(protocol.strip_echoes(self.buf))
        self.buf = ""


def agent_loop(activity):
    try:
        out = _agent_loop(activity)
    except Stopped:
        # Stopped while the brain was thinking or between steps: the history is complete up to here (every tool
        # call has its result), so just close it with a short note and give control back.
        msgs = session.state["messages"]
        if msgs and msgs[-1]["role"] != "assistant":
            msgs.append({"role": "assistant", "content": STOPPED_REPLY})
        session.think("🛑 Stopped.", cloud_too=False)
        activity.append("Stopped")
        if session.state.get("task") is not None:
            session.state["task"]["stopped"] = True      # the episode log: a stopped task never counts as one that worked
            task_state.mark(task_state.of(session.state["task"]), "stopped", session.state.pop("stop_why", "Stop pressed"))
        out = {"reply": STOPPED_REPLY, "activity": activity, "say": STOPPED_REPLY, "stopped": True}
    resume.save()                                     # the card as the task ended (done / failed / stopped / waiting)
    voice_io.nap["last_task"] = time.time()          # the 5-minute nap clock counts from when she finished, not from the ask
    out["images"] = session.state.pop("images", [])
    if session.state.get("check_image"):
        out["images"].append(session.state.pop("check_image"))
    return out


DONE_CLAIM_RE = re.compile(r"(all set|ready (for|to)\b|is ready|are ready|\bi['’]?ve (just )?(opened|started|turned|set|closed|pushed|sent|"
                           r"launched|enabled|switched|made|done|put|muted|played)|\b(opened|started|launched|enabled)\b[ !.]|\bdone[!.])", re.I)


# Her own reply promising an action she didn't take. The small models write a curly ’ as often as a straight ' -
# "I’ll check that right now." with no tool call slipped through (2026-11-23), as did every "check/look/find" verb.
# "let me know..." is a question to the user, not a promise.
PROMISE_RE = re.compile(
    r"\b(i['’]?ll|i will|i['’]?m|i am|let me(?! know)|going to|now)\b[^.!?]{0,30}\b(click|open|search|play|scroll|type|"
    r"press|clos|select|tap|launch|start|go to|navigat|lock|mut|paus|turn|set|shut|restart|remind|delet|mov|install|"
    r"check|look|find|scan|measur)"
    r"|\b(lock|open|clos|play|search|launch|start|turn|mut|paus|set|shut|restart|delet|mov|install|clean|check|scan|"
    r"measur)\w*ing\b[^.!?]{0,30}\b(now|right away)\b|\bi['’]?ll do (it|that) (right away|now)\b", re.I)


# "Reacting now... I'm dancing!" with no react call: her body never moved (2026-11-18 log).
BODY_CLAIM_RE = re.compile(r"\b(?:i'?m|i am|i'?ll|let me|watch me)\s+(?:now\s+|just\s+|still\s+)?(?P<v>dancing|dance|clapping|clap|"
                           r"spinning|spin|twirling|laughing|waving|bowing)\b|\breacting now\b|"
                           r"\bi (?:just )?(?:did a (?:little )?)?(?P<w>danced|clapped|spun|dance|spin|clap)\b", re.I)


REPEAT_LIMIT = 3      # the same tool call and result this many times in a row: she's told to stop; once more = the end


REPEATABLE = {"scroll", "press_keys", "type_text", "media_control", "video_control", "set_volume", "set_brightness",
              "click_at", "click_nth", "click_element", "react", "drag_window"}   # doing these again really does more


def _agent_loop(activity):
    """Run the model until it answers, or until it wants to do something risky."""
    s = config.settings()
    model = config.brain(s)
    spoken = []
    lead_ins = set()     # which of spoken were said just before a tool step ("Let me check:")
    nudged = did_something = claim_nudged = False
    last_calls = last_outs = None        # the same tool call, with the same result, again and again = stuck in a loop
    same_calls = 0
    loose = {}          # 4d's progress record when the request has no task
    unsaid = []      # reply text that has not been spoken yet (text spoken while it was being written isn't here)

    def result(reply, **extra):
        stage = shared.stage_reaction(shared.stage_directions(reply or "")[1])   # '[react: "laugh"]' -> after_turn plays it
        if stage:
            extra.setdefault("stage_reaction", stage)
        reply = re.sub(r"[ \t]{2,}", " ", shared.REACT_MARKER_RE.sub(" ", reply or "")).strip()   # ...and not on screen
        reply = no_self_greeting(reply)
        say = " ".join(unsaid).strip()
        if not say and not spoken:
            say = reply                     # e.g. the plain "Done!"
        return {"reply": reply, "activity": activity, "say": say, **extra}

    def ask_back(asked, *said):         # 4d / 4e: a question card ends the turn, after whatever she said
        unsaid.extend(t for t in said + (asked["reply"],) if t)
        return result(" ".join(spoken + [t for t in said if t] + [asked["reply"]]).strip(), question=asked["question"])

    turns = 0
    while True:
        check_stop()
        task = session.state.get("task")
        brains.at_start(task, s)                                      # "ask Gemini ...": start this task on the cloud
        if task and task.get("route") and not task.get("routed"):       # chosen from the request's words
            task["routed"] = True
            route_task(task["route"], "request wording")
        if task and task.get("model"):
            model = task["model"]
        deep = (bool(cloud.parse(model)) and prompts.reasoning_level(s, task) != "low") or bool(task and task.get("plan"))   # a planned job has many steps
        # Any tool step makes it a task - not just on-screen clicks. (Only those were counted, so a job done with
        # commands - git, PowerShell, looking at the screen - got the 10-turn limit of a plain question.)
        working = task and (task["steps"] or task.get("calls"))
        if turns >= ((DEEP_TASK_TURNS if deep else MAX_TASK_TURNS) if working else MAX_TURNS):
            break
        if working and time.time() - task.get("started", time.time()) > TASK_SECS:
            task["time_up"] = True                     # 4c: every task has a time limit, too
            break
        turns += 1
        voice_io.nap["last_task"] = time.time()      # working on it: a long task is not "quiet", so no nap mid-job
        msgs = [{"role": "system", "content": prompts.system_prompt(model)}] + trimmed_history(model)
        # Ordinary questions are spoken while the answer is still being written. During a multi-step screen task
        # she must not talk early - her "done!" may still be corrected by her own check of the screen.
        stream = SpeechStream() if not (task and (task["steps"] or task.get("skill"))) and not session.state.get("remote") else None   # 📱: no voice
        session.think("💭 Working out what you need…" if turns == 1 and not did_something else "💭 Deciding the next step…")
        reasoning = session.ThoughtStream()
        level = prompts.reasoning_level(s, task)
        if level != "low" and cloud.parse(model):
            session.think(f"🧠 Thinking this through carefully ({level} reasoning)…", cloud_too=False)
        # If the cloud fails on a request the local router would hand to the coder or the planner, don't let the
        # everyday brain answer it: give up this turn and run it again on that brain (no local answer to throw away).
        local_route = local_route_for(task, s) if cloud.parse(model) else None
        climbed = bool(task and task.get("climbed") and cloud.parse(model))   # brains.py: on the cloud because stuck
        t0, direct = time.time(), (task.pop("direct", None) if task and turns == 1 else None)
        try:
            answer_only = bool(task and task.get("answer_only") and model == task.get("model"))
            stored, text, calls = skillrun.direct_call(model, direct) if direct else interruptible(llm.chat, model, msgs, [] if answer_only else prompts.active_tools(model),
                                                think=s["think"] or (level != "low" and (bool(cloud.parse(model)) or s.get("reasoning", "auto") != "auto")), reasoning=level,   # auto never makes the local brain think: 30-45 s per step on a 6 GB GPU
                                                on_text=unless_stopped(stream.feed if stream else None),
                                                fallback=None if local_route or climbed else s["model"], on_think=unless_stopped(reasoning.feed),
                                                keep_alive=router.BORROW_KEEP if model != s["model"] and not cloud.parse(model) else None)
        except llm.OllamaError as e:
            if not (local_route or climbed):         # a climbed task's history is cloud-sized: rerun the turn locally, trimmed
                raise
            reasoning.flush()
            task["model"], task["route"] = s["model"], local_route
            turns -= 1                                      # this turn produced nothing; it doesn't use up the budget
            if "Used the local brain" not in activity:
                activity.append("Used the local brain")
            task_log(f"CLOUD BRAIN NOT USED ({model}): {e} -> local route '{local_route}' now")
            session.think("☁️ The cloud brain didn't answer, so I'm handing this to my local "
                  + ("brain to finish." if climbed else "coding brain." if local_route == "code" else "deep-thinking brain to plan it."))
            continue
        reasoning.flush()
        used = s["model"] if llm.LAST["fallback"] else model
        if not direct:                  # a step code chose (skillrun.direct_call) took no brain time to report
            task_log(f"BRAIN {time.time() - t0:.1f}s ({'local fallback' if llm.LAST['fallback'] else model}, {level})")
            session.think(f"⏱ Thought for {time.time() - t0:.1f}s ({used.replace('cloud:', '')})"
                  + (f" → {len(calls)} step{'s' if len(calls) > 1 else ''} to do" if calls else " → answering"),
                  cloud_too=False)
        if llm.LAST["fallback"] and "Used the local brain" not in activity:
            activity.append("Used the local brain")   # the cloud one failed; she carried on with the local model
            task_log(f"CLOUD BRAIN NOT USED ({model}): {llm.LAST['fallback']} -> local {s['model']}")
            session.think("☁️ " + (llm.LAST["fallback"] if "used up" in llm.LAST["fallback"] else "The cloud brain didn't answer, so I used my local one."))
            if task and cloud.parse(task_brain(s, task)):   # finish this task locally, with the local plan/coder help
                task["model"] = s["model"]
                choose_local_route(task, s)
                if task.get("route"):
                    task_log(f"CLOUD FELL BACK -> local route '{task['route']}' from next turn")
        if stream:
            stream.flush()
        session.state["messages"].append(stored)
        if text:
            task_log(f"NOVA SAYS: {text[:300]}")
        did_something = did_something or bool(calls)
        claimed_body = bool(BODY_CLAIM_RE.search(text or "")) and not session.state.get("react_after")
        invent = session.state.get("invent")        # "make one up" - but no new_move call yet (react_after is set by new_move)
        if invent and not calls and not nudged and not session.state.get("react_after"):
            nudged = True
            session.think(f"🙈 I said I'd make the '{invent}' move but didn't - doing it properly now.")
            session.state["messages"].append({"role": "user", "content":
                protocol.note(f"You haven't made the move yet. Call new_move NOW for '{invent}': a short "
                              "name, a duration in seconds, and 4-8 keys (each {\"at\": 0..1, plus poses such as right_arm_out, "
                              "left_arm_out, lean_side, jump, head_nod}). Then say one short line.")})
            continue
        if not calls and not nudged and not (task or {}).get("declined") and (claimed_body or (not did_something and PROMISE_RE.search(text or ""))):
            # Small models sometimes *say* "I'm clicking it now" without calling a tool. Make her follow through.
            nudged = True
            session.think("🙈 I said I'd do it but didn't actually do anything - doing it properly now.")
            session.state["messages"].append({"role": "user", "content":
                protocol.note("You said you'd do something but didn't call a tool, so nothing happened. "
                              "Call the right tool now. If no tool can do it, say so honestly in one sentence.")})
            continue
        rec = task.get("recipe") if task else None
        if not calls and rec and task.get("calls", 0) <= rec["calls"]:
            # She was handed a skill's steps and stopped without calling a single tool: nothing happened.
            if not rec["nudged"]:
                rec["nudged"] = True
                session.think(f"🙈 I haven't done any step of my '{rec['name']}' skill yet - doing it now.")
                session.state["messages"].append({"role": "user", "content":
                    protocol.note(f"You haven't done any step of the skill '{rec['name']}' yet - nothing has "
                                  "happened. Do its first step now with a tool.")})
                continue
            task_log(f"HALLUCINATED DONE: skill '{rec['name']}' handed over, no tool called after it")
            text = f"I didn't actually run my '{rec['name']}' skill - nothing happened. Want me to try again?"
        elif (not calls and not did_something and task and not task.get("calls") and not task["steps"]
              and DONE_CLAIM_RE.search(text or "") and task.get("skill")):
            # "You're all set!" with no tool called anywhere in this request: it can't be true.
            if not claim_nudged:
                claim_nudged = True
                session.think("🙈 I said it was done but I haven't done anything - doing it properly now.")
                session.state["messages"].append({"role": "user", "content":
                    protocol.note("You said it is done, but you have not called any tool, so nothing "
                                  "happened. If the request needs an action, do it now with the right tool (or run_skill for a "
                                  "skill). If you can't, say honestly that you didn't do it.")})
                continue
            task_log(f"HALLUCINATED DONE: '{(text or '')[:120]}' with no tool called")
            text = "I haven't actually done that yet - tell me again and I'll do it properly."
        if not calls and (asked := watching.gather(task)):          # 4d: the questions she queued, on one card
            return ask_back(asked, protocol.strip_meta(text or ""))
        if not calls and task and task["steps"] and (task.get("checks") or task["steps"] >= LOOK_FROM_STEP):
            # She says she's finished: the Judge decides (checks first, at most one advisory screen look).
            judging.final_judge(task, text, activity, model)
        if not calls and task:
            judging.readback_verdict(task)             # 4c: a failed read-back beats every "done"
        by_check = bool(task) and task.get("verdict_source") == "checks"
        if (not calls and task and (task["steps"] >= 2 or by_check) and task["done"] is False
                and not task["nudged"]):
            # She's about to stop, but a check (or her one screen look) says the task isn't finished. One more push.
            task["nudged"] = True
            session.think("🔁 My check says part of it isn't done yet - going back to finish it.")
            session.state["messages"].append({"role": "user", "content":
                protocol.note(("A check shows the task is not finished yet:\n" if by_check else
                               "Your last look at the screen says the task is not finished yet:\n")
                              + f"{task['screen']}\n" + (task_state.render(task_state.of(task)) or f"The task: {task['goal']}\n"
                              "The only steps you have really done so far:\n- " + "\n- ".join(task["did"]))
                              + "\nWork out which part of the task has no step yet and do it now with "
                              "a tool. " + ("You may not say the task is done until the check passes. " if by_check else "")
                              + "If you are truly stuck, tell the user honestly in one sentence what is left.")})
            continue
        if not calls and brains.at_answer(task, text, s):            # local first: stuck -> the cloud brain carries on
            turns = same_calls = 0
            continue
        if not calls and task and task["nudged"] and task["done"] is False:
            # Pushed once and still stopping with the task unfinished.
            if by_check:
                # A failed deterministic check is the truth for every brain: say so plainly, never claim success.
                text = ("I don't think I finished that - " + (task.get("fail_plain") or "a check failed")
                        + ". Want me to try again?")
            elif text and "?" not in text[-40:]:
                # The screen look only advises (it can be wrong, e.g. it calls a written-but-unsent email "not
                # done"): keep her answer and ask instead of overruling it.
                text = text.rstrip() + " My screen check wasn't sure it worked - did it?"
        text = protocol.strip_meta(text)
        if text:
            spoken.append(text)
            if calls:
                lead_ins.add(len(spoken) - 1)
            if not (stream and llm.supports_tools(model)):
                unsaid.append(text)
        if not calls:
            # "Sure! Let's run the command:" said before a step isn't part of the answer once a real answer follows
            # (it read "...in your terminal: Ah! Looks like Python 3.12..."). Out loud she still says both.
            answer_only_text = [t for i, t in enumerate(spoken) if i not in lead_ins]
            task_state.mark(task_state.of(task), "failed" if task and task["done"] is False else "done")
            return result(" ".join(answer_only_text if text else spoken).strip() or "Done!")
        sig = json.dumps([(c["name"], c["args"]) for c in calls], sort_keys=True, default=str)
        repeatable = any(c["name"] in REPEATABLE for c in calls)
        if sig == last_calls and same_calls >= REPEAT_LIMIT and not repeatable:
            # Told to stop repeating and did it again: end here rather than spending the whole step budget on it.
            session.state["messages"].extend(llm.tool_result_message(model, c, "Not run - same as before.") for c in calls)
            task_log(f"STUCK: {calls[0]['name']} called {same_calls} times in a row with the same arguments")
            return ask_back(watching.stuck(task, f"did the same step ({calls[0]['name']}) {same_calls} times with nothing new"))
        if held := watching.outside_order(task, calls, model):      # 4e: an order from outside text: asked, never done
            return ask_back(held)
        if any(session.is_risky(c) for c in calls):
            session.state["pending"] = calls
            task_state.mark(task_state.of(task), "waiting", "an approval")
            session.think("✋ I need your OK before I " + ", ".join((session.ask_text(c) if c["name"] == "run_skill" else session.ASK.get(c["name"], c["name"])).lower()
                                                            for c in calls if session.is_risky(c)) + ".")
            return result(" ".join(spoken).strip(),
                          pending=[{"tool": c["name"], "label": session.ask_text(c), "input": c["args"]}
                                   for c in calls if session.is_risky(c)])
        if task is not None:
            task["calls"] = task.get("calls", 0) + len(calls)
        session.state["messages"].extend(judging.execute(calls, True, activity, model))
        handed = session.state.pop("hand_off", None)
        if handed:                          # hand_off_job opened a question card: that's the reply, nothing more
            unsaid.append(handed["reply"])
            return result(handed["reply"], **{k: handed[k] for k in ("question", "brief") if k in handed})
        pause = session.state.pop("skill_pause", None)
        if pause:                           # a skill asks the user something (confirm / ask steps)
            asked = skillrun.ask_skill_question(pause)
            unsaid.append(asked["reply"])
            return result(asked["reply"], pending=asked["pending"])
        final = session.state.pop("skill_final", None)
        if final and all(c["name"] == "run_skill" for c in calls):
            session.state["messages"].append({"role": "assistant", "content": final})
            unsaid.append(final)
            return result(final)
        # Compare only what the tool itself said: the "[check after step N]" note added to it changes every step.
        outs = json.dumps([str(o).split(protocol.CHECK_NOTE_START)[0] for o in session.state.get("last_outs") or []])
        same = sig == last_calls and outs == last_outs and not repeatable
        same_calls, last_calls, last_outs = (same_calls + 1 if same else 1), sig, outs
        if brains.after_step(task, s, same_calls, turns):            # local first: stuck -> the cloud brain carries on
            turns = same_calls = 0
            watching.fresh(task)                                     # ...and 4d counts its progress from there
            continue
        if stuck := watching.after_step(task if task is not None else loose, calls):   # 4d: stuck after the new plan
            return ask_back(stuck)
    if task is not None:
        task["paused"] = True               # "keep going" now carries on with this task
        task_state.mark(task_state.of(task), "stopped", "time limit" if task.get("time_up") else "out of steps")
    session.think("🛑 That's taking too long - pausing here." if task and task.get("time_up") else "🛑 That's a lot of steps - pausing here.")
    line = ("That took over 10 minutes" if task and task.get("time_up") else "That was a lot of steps") + \
        ", so I paused here. Say “keep going” and I'll carry on."
    unsaid.append(line)
    return result((" ".join(spoken) + f" ({line})").strip())


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import body, brains, config, judging, learning, prompts, resume, session, skillrun, voice_io, watching  # noqa: E402
