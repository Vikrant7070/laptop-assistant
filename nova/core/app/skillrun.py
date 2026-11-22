"""Running a saved skill step by step, pausing / resuming it, and saving a finished task as a skill."""


import json
import re
import time

from nova import prompts as promptfiles
from nova.brain import cloud
from nova.brain import llm
from nova.actions import skills
from nova.actions import checks
from nova.actions import recorder
from nova.brain import toolpick
from nova.actions import tools
from nova import protocol


FAIL_RE = re.compile(r"^\W*(error|not done|no open window|no '|i couldn'?t|couldn'?t|i can'?t|can'?t|failed|the user declined)", re.I)


SKILL_BRAIN_TOOLS = ["look_at_screen", "list_clickable", "click_element", "press_keys", "type_text", "window_action",
                     "read_page", "video_control"]


SKILL_BRAIN_TURNS = 4


def _failed(out):
    first = (str(out).strip().splitlines() or [""])[0]
    return bool(FAIL_RE.search(first))


def _short(text, n=160):
    return ((str(text).strip().splitlines() or [""])[0])[:n]


def _sfill(text, run):
    """{input}, {input_url} and the slots earlier steps filled ({message}) in a step's text."""
    out = skills.fill({"x": text}, run["input"])["x"]
    for k, v in run["slots"].items():
        out = out.replace("{" + k + "}", v)
    return out


def _fill_args(args, run):
    return {k: (_sfill(v, run) if isinstance(v, str) else v) for k, v in args.items()}


def skill_brain_step(s, instruction, names, run, model, approved, activity, needs_tool=True):
    """One bounded model call for ONE step of a skill. The model sees only this instruction, what the skill has done
    so far and the few tools the step names - never the whole conversation - so even the small local model can do
    it. Returns (ok, its one-sentence answer)."""
    cfg = config.settings()
    sch = toolpick.schemas([n for n in names if n not in skills.LOCKED])
    proto = "" if llm.supports_tools(model) or not sch else llm.text_protocol(sch)
    sofar = "; ".join(f"{k}: {v}" for k, v in list(run["results"].items())[-6:]) or "nothing yet"
    sysmsg = promptfiles.load("skill_step", name=s["name"], instruction=instruction, sofar=sofar) + proto
    msgs = [{"role": "system", "content": sysmsg}, {"role": "user", "content": "Go."}]
    text, called = "", 0
    for _ in range(SKILL_BRAIN_TURNS):
        conversation.check_stop()
        stored, text, calls = conversation.interruptible(llm.chat, model, msgs, sch, fallback=cfg["model"] if cloud.parse(model) else None)
        msgs.append(stored)
        if not calls:
            break
        called += len(calls)
        msgs.extend(judging.execute(calls, approved, activity, model))
    text = (text or "").strip()
    if text.upper().startswith("STOP"):
        return False, text
    if needs_tool and sch and not called:
        return False, "I didn't actually do anything for that step."
    return True, text or "done"


def skill_screen_ok(s, step, out):
    """A fresh look at the screen for a step that has no structured check (setting skill_screen_checks, off by
    default: each look costs seconds on the laptop's GPU). It must SEE that the step worked - an answer it can't
    make out counts as not worked. A look that can't happen at all isn't held against the step."""
    try:
        img = tools._grab_screen_jpeg()
        seen = conversation.interruptible(llm.see, img, conversation.CHECK_PROMPT.format(goal=s["description"] or s["name"], step=step,
                                                              result=out[:200], windows=tools.open_windows_summary(4)),
                             preferred=config.settings()["model"], kind="check")
    except conversation.Stopped:
        raise
    except Exception as e:
        return True, f"(couldn't look: {e})"
    verdict = protocol.parse_worked(seen)
    return verdict["worked"], verdict["screen"]


def _skill_say(run, text):
    run["said"].append(_sfill(text, run))


def _pause_skill(s, run, question, resume_at):
    """The skill asks the user something: stop here, keep where we are, and let _agent_loop / confirm_pending ask."""
    session.state["skill_run"] = run
    session.state["skill_pause"] = {"name": s["name"], "question": question, "resume_at": resume_at, "input": run["input"]}
    return f"Paused my '{s['name']}' skill to ask the user: {question}"


def _skill_report(run, instruction, model):
    """The last `report` step: the brain words the answer from what the steps found."""
    found = "\n".join(f"- {k}: {v}" for k, v in run["report"][-8:])[:3000]
    msgs = [{"role": "system", "content": "You are Nova. Answer the user briefly and warmly from the results below. "
                                          "Use only what the results say."},
            {"role": "user", "content": f"{instruction}\n\nResults:\n{found}"}]
    try:
        _, text, _ = conversation.interruptible(llm.chat, model, msgs, [], fallback=config.settings()["model"] if cloud.parse(model) else None,
                                   num_predict=220)
        return (text or "").strip()
    except conversation.Stopped:
        raise
    except Exception as e:
        conversation.task_log(f"SKILL REPORT failed: {type(e).__name__}: {e}")
        return ""


def run_skill(args, approved, activity, model):
    """Run a saved skill. A routine runs here, step by step, in code: each step is checked, and the final "done" is
    worded by this code from what really happened - never by the model. A recipe (old, plain-English) hands its
    instructions to the brain."""
    s = skills.get(args.get("name", ""))
    if not s:
        return f"There is no skill called '{args.get('name', '')}'. " + skills.describe()
    if s["kind"] == "broken":
        return f"The skill '{s['name']}' can't run: {s['error']}. Its file is {s['file']}."
    task = session.state.get("task")
    if task is not None:
        task["ran_skill"] = s["name"]        # the episode log: this task used a skill, so it isn't offered as a new one
    activity.append(session.FRIENDLY["run_skill"])
    if s["kind"] == "recipe":
        session.think(f"📜 Following my '{s['name']}' skill.")
        if task and task.get("tools"):
            task["tools"] = [t["name"] for t in toolpick._all() if t["name"] in set(task["tools"]) | set(s["tools"])]
        if task is not None:                 # if she stops without having done any step, say so (_agent_loop)
            task["recipe"] = {"name": s["name"], "calls": task.get("calls", 0), "nudged": False}
        return f"Follow your skill '{s['name']}' now, one tool call at a time:\n{s['body']}"
    steps = skills._ids_for([dict(x) for x in s["steps"]])
    ids = [st["id"] for st in steps]
    text = str(args.get("input") or "").strip()
    resume_at = args.get("resume_at")
    if resume_at is not None:                # the user answered the skill's own question
        run = session.state.get("skill_run")
        if not run or run.get("skill") != s["name"]:
            return f"I lost track of where my '{s['name']}' skill was - ask me to run it again."
        i = ids.index(resume_at) if resume_at in ids else len(ids)
        run["stopped"] = ""
        if run.get("asked"):                 # "I've signed in": the user fixed the step that failed
            run["failed"] = [f for f in run["failed"] if f[0] != run["asked"]]
            run["ok"].append(run["asked"])
            run["asked"] = ""
    else:
        if s["needs_input"] and not text:
            return f"The skill '{s['name']}' needs to know what it's for this time - ask the user, then call it with input."
        run = {"skill": s["name"], "version": s["version"], "input": text, "results": {}, "slots": {}, "said": [],
               "ok": [], "failed": [], "report": [], "tries": {}, "stopped": ""}
        session.think(f"📜 Running my '{s['name']}' skill ({len(steps)} steps).")
        i = 0
    watch = learning._can_teach(s) and not run.get("taught") and not recorder.recording()
    if watch:                       # notice (only THAT) the user clicks or types while the skill works
        recorder.start(mode="listen")
    try:
        return _skill_loop(s, steps, ids, run, i, approved, activity, model, task, watch)
    finally:
        if watch:
            recorder.stop_listening()


def _skill_loop(s, steps, ids, run, i, approved, activity, model, task, watch):
    """The steps of one skill run, from step index i (see run_skill)."""
    report_instr = ""
    last_done, t_mark = None, 0.0
    while i < len(steps):
        conversation.check_stop()
        st = steps[i]
        sid = st["id"]
        if watch and last_done and recorder.user_acted_since(t_mark):
            return learning._takeover_pause(s, run, last_done, sid)
        if "say" in st:
            _skill_say(run, st["say"])
            i += 1
            continue
        if "wait" in st:
            time.sleep(st["wait"])
            i += 1
            continue
        if "if" in st:
            ok_, _seen = checks.evaluate(st["if"], {"window": run.get("window", "")}, timeout=3)
            i = ids.index(st["goto"]) if ok_ else i + 1
            continue
        if "confirm" in st:
            return _pause_skill(s, run, _sfill(st["confirm"], run), ids[i + 1] if i + 1 < len(ids) else "")
        if "report" in st:
            report_instr = _sfill(st["report"], run)
            i += 1
            continue
        # ---- a step that does something
        step_started = time.time()
        session.think(f"📜 Step {sid}: " + (st.get("tool") or "ask my brain") + (f" ({session._short_args(st['args'])})" if st.get("args") else ""))
        ctx = {"window": ""}
        if "brain" in st:
            ok_, out = skill_brain_step(s, _sfill(st["brain"], run), st.get("tools") or SKILL_BRAIN_TOOLS, run, model,
                                        approved, activity, needs_tool=not st.get("slot"))
            if ok_ and st.get("slot"):
                run["slots"][st["slot"]] = out.strip().strip("\"'")[:200]
            label = "brain"
        else:
            call = {"name": st["tool"], "args": _fill_args(st["args"], run)}
            ctx["window"] = str(call["args"].get("window") or call["args"].get("title_or_app") or "")
            if session.is_risky(call) and not approved:
                out, ok_ = "Not done - it needs the user's OK.", False
            else:
                out = tools.run_tool(call["name"], call["args"])
                ok_ = not _failed(out)
                activity.append(session.FRIENDLY.get(call["name"], call["name"]))
                if task is not None and call["name"] not in task.setdefault("used", []):
                    task["used"].append(call["name"])
                if call["name"] in conversation.ACT_TOOLS and ok_:
                    judging.settle(call["name"], quick=True)      # the next step needs the window this one opened
            label = call["name"]
            conversation.task_log(f"  SKILL STEP {sid} {call['name']} {json.dumps(call['args'], ensure_ascii=False)[:200]} -> {out[:200]}")
        ctx["result"] = out
        if ok_ and st.get("expect"):                # the code checks the result itself; the tool saying "ok" isn't enough
            ok_, seen = checks.evaluate(st["expect"], ctx)
            conversation.task_log(f"  SKILL CHECK {sid} {skills.render_check(st['expect'])} -> {'ok' if ok_ else 'NOT ok'} ({seen})")
            if not ok_:
                out = f"{_short(out, 80)} - but the check '{skills.render_check(st['expect'])}' failed: {seen}"
        elif (ok_ and "tool" in st and st["tool"] in conversation.ACT_TOOLS and config.settings().get("skill_screen_checks")
              and tools._screen_allowed()):
            ok_, seen = skill_screen_ok(s, f"{st['tool']} {session._short_args(call['args'])}", out)
            conversation.task_log(f"  SKILL SCREEN CHECK {sid} -> {'ok' if ok_ else 'NOT ok'} ({seen[:120]})")
            if not ok_:
                out = f"{_short(out, 80)} - but the screen doesn't show it worked: {seen[:100]}"
        last_done, t_mark = sid, step_started      # the user acting from now on means they took over after this step
        if ok_:
            run["ok"].append(sid)
            run["results"][sid] = f"{label}: {_short(out)}"
            if out.strip():
                run["report"].append((sid, out.strip()[:700]))
            if ctx["window"]:
                run["window"] = ctx["window"]
            i += 1
            continue
        # ---- it didn't work: what the step says to do (default: stop)
        run["results"][sid] = f"{label}: {_short(out)}"
        acts, nxt, handled = st.get("on_fail") or [{"do": "stop"}], i + 1, False
        for a in acts:
            d = a["do"]
            if d == "retry":
                if run["tries"].get(sid, 0) < a["n"]:
                    run["tries"][sid] = run["tries"].get(sid, 0) + 1
                    session.think(f"🔁 Step {sid} didn't work - trying again.", cloud_too=False)
                    nxt, handled = i, True
                    break
                continue
            if d == "brain":
                fixed, _ = skill_brain_step(s, _sfill(a["instr"], run), SKILL_BRAIN_TOOLS, run, model, approved, activity)
                if fixed and (not st.get("expect") or checks.evaluate(st["expect"], ctx)[0]):
                    run["ok"].append(sid)
                    handled = True
                    break
                continue
            if d == "goto":
                run["failed"].append((sid, label, _short(out)))
                nxt, handled = ids.index(a["id"]), True
                break
            if d == "ask":
                run["failed"].append((sid, label, _short(out)))
                run["asked"] = sid
                return _pause_skill(s, run, _sfill(a["q"], run), ids[i + 1] if i + 1 < len(ids) else "")
            if d == "skip":
                run["failed"].append((sid, label, _short(out)))
                handled = True
                break
            if d == "stop":
                run["failed"].append((sid, label, _short(out)))
                run["stopped"] = _sfill(a.get("msg") or f"step {sid} ({label}) didn't work: {_short(out, 100)}", run)
                break
        if run["stopped"]:
            break
        if not handled:                       # only retries, all used up
            run["failed"].append((sid, label, _short(out)))
            run["stopped"] = f"step {sid} ({label}) didn't work: {_short(out, 100)}"
            break
        i = nxt
    if watch and last_done and not run["stopped"] and recorder.user_acted_since(t_mark):
        return learning._takeover_pause(s, run, last_done, "")
    return _finish_skill(s, run, report_instr, model)


def _finish_skill(s, run, report_instr, model):
    """Word the end of a skill run from what really happened, and count it."""
    n_failed = len(run["failed"])
    if run["stopped"]:
        final = f"I stopped my '{s['name']}' skill: {run['stopped']}"
    elif n_failed:
        bad = "; ".join(f"step {sid} ({tool}) didn't work: {why}" for sid, tool, why in run["failed"][:3])
        final = f"I ran my '{s['name']}' skill, but not everything worked - {bad}."
    elif report_instr:
        final = _skill_report(run, report_instr, model) or " ".join(run["said"]).strip() or f"I ran my '{s['name']}' skill."
    else:
        final = " ".join(run["said"]).strip() or f"Done - I ran my '{s['name']}' skill."
    ok = not run["stopped"] and not n_failed
    first = run["failed"][0] if run["failed"] else ("", "", "")
    if not run.get("counted"):           # a run that was paused to learn a step is counted once
        note = skills.record_run(s["name"], ok, failed_step=first[0], error=run["stopped"] or first[2])
        run["counted"] = True
        if note:
            final += " " + note
    session.state["last_skill"] = {"name": s["name"], "ts": time.time(), "result": "worked" if ok else f"failed at {first[0] or 'a step'}"}
    if run["stopped"] and run["failed"] and not run.get("taught") and learning._can_teach(s) and skills.get(s["name"]):
        sid = run["failed"][-1][0]           # "show me how to do that step" - then the skill carries on after it
        ids = [x["id"] for x in skills._ids_for([dict(y) for y in s["steps"]])]
        nxt = ids[ids.index(sid) + 1] if sid in ids and ids.index(sid) + 1 < len(ids) else ""
        session.state["skill_run"] = run
        session.state["skill_pause"] = {"name": s["name"], "input": run["input"], "resume_at": nxt,
                                "question": f"{final} Want to show me how to do step {sid}? Say yes, do it yourself, then say 'done'.",
                                "teach": {"phase": "show", "sid": sid, "mode": "replace"}}
        conversation.task_log(f"SKILL DONE {s['name']} ok=False - offering to learn step {sid} from the user")
        return f"Paused my '{s['name']}' skill: step {sid} failed, asking the user to show it."
    session.state.pop("skill_run", None)
    session.state["skill_final"] = final
    conversation.task_log(f"SKILL DONE {s['name']} ok={ok} ran={len(run['ok'])} failed={n_failed} :: {final[:200]}")
    lines = [f"{sid}: {txt}" for sid, txt in run["results"].items()]
    return (f"Ran the skill '{s['name']}'" + (f" - {n_failed} step(s) didn't work" if n_failed else "") + ":\n"
            + "\n".join(lines) + f"\nTell the user exactly this: {final}\nDon't redo any of these steps.")


def resume_skill(args, approved, activity):
    """The user answered a question a skill asked. Returns the answer dict (like _agent_loop's result)."""
    model = config.brain()
    teach = args.get("teach") or {}
    name = args.get("name", "")
    if teach.get("phase") == "show":         # "want to show me how it should go?"
        run = session.state.get("skill_run") or {}
        if not approved:
            session.state.pop("skill_run", None)
            if not run.get("counted"):
                skills.record_run(name, False, stopped=True)
            return learning._say_final(f"Okay, I stopped my '{name}' skill there.", activity)
        session.state["teach"] = {"skill": name, "sid": teach.get("sid", ""), "mode": teach.get("mode", "insert"),
                          "resume_at": args.get("resume_at", ""), "input": args.get("input", "")}
        msg = learning.start_watching_now(f"step {teach.get('sid', '')} of my '{name}' skill")
        if recorder.recording():
            return learning._say_final(msg, activity)
        session.state.pop("teach", None)             # couldn't watch: carry on with the skill instead
        args = {"name": name, "input": args.get("input", ""), "resume_at": args.get("resume_at", "")}
        approved = True
    elif teach.get("phase") == "apply":      # "change my skill like this?"
        msg = (skills.edit(name, teach.get("ops", []), author="recorder", cause="shown by the user") if approved
               else "Okay, I left the skill as it was.")
        conversation.task_log(f"SKILL TAUGHT {name}: {msg[:200]}")
        if not session.state.get("skill_run"):
            return learning._say_final(msg, activity)
        out = run_skill({"name": name, "input": args.get("input", ""), "resume_at": args.get("resume_at", "")},
                        True, activity, model)
        pause = session.state.pop("skill_pause", None)
        if pause:
            pause["question"] = msg + " " + pause["question"]
            return ask_skill_question(pause)
        return learning._say_final(msg + " " + (session.state.pop("skill_final", None) or out), activity)
    if not approved:
        run = session.state.pop("skill_run", None) or {}
        skills.record_run(args.get("name", ""), False, stopped=True)
        final = f"Okay, I stopped my '{args.get('name', '')}' skill there."
    else:
        out = run_skill(args, True, activity, model)
        pause = session.state.pop("skill_pause", None)
        if pause:
            return ask_skill_question(pause)
        final = session.state.pop("skill_final", None) or out
    session.state.pop("skill_final", None)
    session.state["messages"].append({"role": "assistant", "content": final})
    return {"reply": final, "activity": activity, "say": final}


def ask_skill_question(pause, append=True):
    """Put a skill's own question to the user as an Allow / No box. append=False: the caller adds the message."""
    args = {"name": pause["name"], "input": pause["input"], "resume_at": pause["resume_at"]}
    if pause.get("teach"):
        args["teach"] = pause["teach"]
    session.state["pending"] = [{"id": "skill-resume", "name": "run_skill", "args": args}]
    if append:
        session.state["messages"].append({"role": "assistant", "content": pause["question"]})
    return {"reply": pause["question"], "activity": [], "say": pause["question"],
            "pending": [{"tool": "run_skill", "label": pause["question"], "input": args}]}


def _trace(c, out):
    """Remember what this task really did (tool, arguments, whether it worked): "remember that" turns it into a skill."""
    task = session.state.get("task")
    if task is None or c["name"] in skills.LOCKED:
        return
    task.setdefault("trace", []).append({"tool": c["name"], "args": dict(c["args"] or {}), "ok": not _failed(out),
                                         "out": _short(out), "t": time.time()})
    del task["trace"][:-60]


def skill_hints(goal, steps):
    """Optional help from the cloud brain when a task becomes a skill: which typed words should be {input}, and what
    to check after a step. It only suggests; skills.draft_from_steps checks every suggestion. {} without a cloud brain."""
    s = config.settings()
    m = config.brain(s)
    if not cloud.parse(m) or llm.cloud_resting():
        return {}
    listing = "\n".join(f"{st['id']}. {skills.render_step(st)}" for st in steps)
    prompt = promptfiles.load("skill_hints", goal=goal[:300], listing=listing)
    try:
        _, text, _ = conversation.interruptible(llm.chat, m, [{"role": "user", "content": prompt}], [], fallback=None)
        found = re.search(r"\{.*\}", text or "", re.S)
        return json.loads(found.group(0)) if found else {}
    except conversation.Stopped:
        raise
    except Exception as e:
        conversation.task_log(f"SKILL HINTS failed: {type(e).__name__}: {e}")
        return {}


def save_task_as_skill_now(args):
    """"Remember that": turn the steps of the task that just finished into a draft skill."""
    last = session.state.get("last_trace")
    if not last:
        return "I haven't finished a task I could save yet - do it once, then say 'remember that'."
    steps, why = skills.steps_from_trace(last["trace"], last["goal"])
    if not steps:
        return why
    hints = skill_hints(last["goal"], steps)
    msg = skills.draft_from_steps(args.get("name", ""), args.get("description", ""), args.get("examples", ""), steps,
                                  hints, last["goal"])
    conversation.task_log(f"SKILL FROM TASK: {msg[:200]}")
    if msg.startswith("Saved"):
        last["from"] = "saved"               # done: the "what should I call it" note stops
    return msg


# ------------------------------------------------------------------ an exact skill match runs without a brain turn

def plan_direct(task, skill, how):
    """new_task: an exact match on a routine that the request names and nothing more -> its run_skill call is the
    first step (task["direct"]), so no brain turn comes before it. Live 2026-11-21: on an exact 'web-research' match
    the small brain listed windows and focused the user's Netflix window before - or instead of - running it."""
    args = skills.direct_args(skill, task.get("goal", "")) if how == "exact" else None
    if args:
        task["direct"] = {"id": "direct_0", "name": "run_skill", "args": args}
        conversation.task_log(f"SKILL DIRECT: run_skill {json.dumps(args, ensure_ascii=False)}")
    return bool(args)


def direct_call(model, call):
    """The planned first step, stored in the history as the brain's own call would be (so later turns read it like
    any other) -> (stored, text, calls), what llm.chat returns. The usual approvals still apply (is_risky)."""
    llm.LAST["fallback"] = None
    session.think(f"📜 Running my '{call['args']['name']}' skill straight away.", cloud_too=False)
    if llm.supports_tools(model):
        stored = {"role": "assistant", "content": "",
                  "tool_calls": [{"function": {"name": call["name"], "arguments": call["args"]}}]}
    else:
        stored = {"role": "assistant", "content": json.dumps({"name": call["name"], "arguments": call["args"]})}
    return stored, "", [call]


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import config, conversation, judging, learning, session  # noqa: E402
