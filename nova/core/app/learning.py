"""Learning from finished work: the episode log and Learner, daily questions, "watch me" recordings and the knowledge bank's review of each turn."""


import json
import re
import time

from nova import runtime
from flask import jsonify, send_from_directory

from nova import prompts as promptfiles
from nova import store
from nova.avatar import avatar_client
from nova.avatar import body as body_module
from nova.actions import knowledge
from nova.brain import llm
from nova.actions import skills
from nova.actions import learner
from nova.actions import lessons
from nova.actions import questions
from nova.actions import recorder
from nova.actions import tools
from nova import shared
from nova import intents
from nova import protocol
from nova.shared import REACTIONS
from nova.core.app import api


LEARN_IDLE, LEARN_EVERY = 60, 300      # seconds quiet before the Learner runs / between its runs


QUESTION_SECS = 600                     # a question she asked waits this long for a yes / no


learn_state = {"last": 0.0}


CORRECTED = "the user corrected Nova or was unhappy with what she did"
LESSON_SECS = 120                       # the review brain's better wording replaces a code lesson this soon after it


def record_episode(task, next_goal=""):
    """Write a finished task to the episode log (nothing for a chat with no steps). With the user's next request,
    a correction of that task becomes a lesson on the daily list (4f, lessons.py). Never raises."""
    if next_goal:
        try:
            qid = lessons.from_turn(task, next_goal)
            if qid:
                session.state["lesson_q"] = {"id": qid, "ts": time.time()}
                conversation.task_log(f"LESSON queued for the daily list (#{qid})")
        except Exception as e:
            conversation.task_log(f"LESSON not queued: {type(e).__name__}: {e}")
    if not task or not task.get("goal"):
        return
    try:
        ep = learner.episode_from_task(task, brain=task.get("model") or config.brain())
        if ep:
            learner.record(ep)
            task["recorded"] = len(task.get("trace") or [])
    except Exception as e:
        conversation.task_log(f"EPISODE not saved: {type(e).__name__}: {e}")


def _covered_by_skill(goal):
    try:
        return skills.pick(goal)[1] == "exact"
    except Exception:
        return False


def learn_due(now=None):
    now = now or time.time()
    return (now - voice_io.nap["last_task"] >= LEARN_IDLE and now - learn_state["last"] >= LEARN_EVERY and not session.lock.locked()
            and not voice_io.convo["thinking"] and not voice_io.mouth.busy() and not session.state.get("pending"))


def learn_now():
    """The idle-time pass: the current task goes to the episode log if it has new steps, then the Learner looks at
    what's new (skill offers go on the daily question list; research site facts are learned as jobs finish)."""
    learn_state["last"] = time.time()
    task = session.state.get("task")
    if task and not task.get("paused") and len(task.get("trace") or []) != task.get("recorded"):
        record_episode(task)
    made = learner.learn(is_skill=_covered_by_skill)
    if made:
        conversation.task_log(f"LEARNER queued {len(made)} question(s) for the daily list: {made}")
    return made


def question_line(now=None):
    """The question to ask at the end of this answer (the daily list, one at a time, with its reason), or ""."""
    if session.state.get("question") or session.state.get("pending") or (integrations.offer["job"] and integrations.offer["until"] > time.time()):
        return ""
    q = questions.due(now)
    if not q:
        return ""
    questions.mark_asked(q["id"], now)
    session.state["question"] = {"id": q["id"], "until": (now or time.time()) + QUESTION_SECS}
    conversation.task_log(f"QUESTION #{q['id']} asked: {q['text'][:200]}")
    lead = "One question from my learning" if not q["urgent"] else "I need you for one thing"
    return f"{lead}: {q['text']} (I'm asking because {q['why']}.)"


def take_question(text, force=None):
    """'yes' / 'no' right after her question from the daily list. Returns her answer, or None if this isn't one."""
    pend = session.state.get("question")
    if not pend:
        return None
    if time.time() > pend["until"]:
        session.state.pop("question", None)
        return None
    clean = config._unspoken(text).strip()
    short = len(clean.split()) <= 8
    yes = force is True or (force is None and short and bool(intents.ANSWER_YES.search(clean)))
    no = force is False or (force is None and short and bool(intents.NO.search(clean)) and not yes)
    if not (yes or no):
        return None             # something else: the question stays on the list for another day
    session.state.pop("question", None)
    q = questions.get(pend["id"])
    if not q:
        return None
    questions.answer(q["id"], "yes" if yes else "no", clean)
    conversation.task_log(f"QUESTION #{q['id']} answered {'yes' if yes else 'no'}")
    voice_io.note_task()
    line = accept_question(q) if yes else "Okay, I won't." if q["kind"] in ("save_skill", "lesson") else "Okay."
    nxt = question_line()
    return line + (" " + nxt if nxt else "")


def accept_question(q):
    """A yes (said, or ✓ on the Knowledge page) to a question from the daily list. -> her answer."""
    if q["kind"] == "save_skill":
        return save_offered_skill(q["data"])
    if q["kind"] == "lesson":                   # 4f: only now is the lesson used
        return lessons.accept(q)
    return "Thanks - noted."


def save_offered_skill(data):
    """A yes to "save it as a skill?": the Learner's steps become a draft skill (the normal skills v2 path - it
    becomes a real skill after it has worked twice)."""
    base = data.get("name") or "saved-task"
    name = base
    for n in range(2, 9):
        if not (store.current().skills_dir / f"{name}.md").exists():
            break
        name = f"{base}-{n}"
    msg = skills.draft_from_steps(name, "", data.get("examples") or [], [dict(st) for st in data.get("steps") or []],
                                  {}, data.get("goal", ""))
    conversation.task_log(f"SKILL FROM LEARNER: {msg[:200]}")
    return msg


def _asked_to_research(task):
    """Did the user's request ask for research / looking something up? (Explaining a topic is not.)"""
    goal = (task or {}).get("goal") or ""
    return bool(intents.RESEARCH.search(config._unspoken(goal)))


def watch_command(text):
    """'watch me' / 'done' -> the answer dict, or None if the request is something else."""
    t = config._unspoken(text).strip()
    if recorder.recording() and intents.WATCH_DONE.search(t):
        return _watch_answer(finish_watching())
    if intents.WATCH_START.search(t):
        return _watch_answer(start_watching_now(t))
    return None


def _watch_answer(text):
    pause = session.state.pop("skill_pause", None)          # "change the skill like this?" after showing a step
    if pause:
        return skillrun.ask_skill_question(pause, append=False)
    return {"reply": text, "activity": ["Watched you" if "Got it" in text else "Started watching"], "say": text}


def start_watching_now(goal=""):
    if session.state.get("remote"):
        return "I can only watch you when you're at the laptop."
    if recorder.recording():
        return "I'm already watching - say 'done' when you're finished."
    recorder.stop_listening()
    err = recorder.start(goal=goal, mode="record")
    if err:
        return err
    conversation.task_log(f"WATCHING started: {goal[:100]}")
    session.think("👀 Watching how you do it - say 'done' when you're finished.")
    if avatar_client.running():
        avatar_client.send("say", text="👀 Watching - say 'done' when you're finished", secs=6)
    teach = session.state.get("teach")
    if teach:
        return (f"Okay, show me how step {teach['sid']} of my '{teach['skill']}' skill should go - do it now, then say "
                "'done'. (I never look at passwords or codes.)")
    return ("Okay, show me! I'm watching your clicks and typing - say 'done' when you're finished. "
            "(I never look at passwords or codes.)")


def finish_watching():
    """Stop watching. Plain 'watch me': the steps wait for a name (save_task_as_skill). Showing one step of a skill:
    propose the change to that skill (an Allow / No question, via state['skill_pause'])."""
    events = recorder.stop()
    goal, hit = recorder.goal(), recorder.limit_hit()
    trace = recorder.events_to_trace(events)
    conversation.task_log(f"WATCHING stopped: {len(events)} events -> {len(trace)} steps")
    teach = session.state.pop("teach", None)
    steps, why = skills.steps_from_trace(trace, goal)
    if teach:
        return teach_propose(teach, steps, why)
    if not steps:
        return f"I watched, but didn't catch anything I could repeat. {why}".strip()
    session.state["last_trace"] = {"goal": goal, "trace": trace, "from": "watching", "ts": time.time()}
    lines = "; ".join(skills.render_step(st)[:60] for st in steps[:6]) + ("; ..." if len(steps) > 6 else "")
    return (f"Got it - I saw {len(steps)} steps: {lines}." + (" (I stopped watching after 10 minutes.)" if hit else "")
            + " What should I call this skill, and what will you say to start it?")


def watch_note():
    """Right after 'watch me ... done': the next request is probably the skill's name."""
    lt = session.state.get("last_trace") or {}
    if lt.get("from") != "watching" or time.time() - lt.get("ts", 0) > 900:
        return ""
    return ("\n\nYou just watched the user do a task and asked what to call it. If they now give it a name (and the "
            "words they'll say to start it), call save_task_as_skill with that name and examples. Don't do the task again.")


def teach_propose(teach, steps, why):
    """The user showed one step of a skill: offer to change that step (replace a failed one, or add what they did)."""
    name, sid = teach["skill"], teach["sid"]
    steps = [st for st in (steps or []) if "report" not in st][:5]
    if not steps:
        session.state.pop("skill_run", None)
        return ("I didn't catch any steps I could use" + (f" ({why})" if why else "")
                + f", so I left my '{name}' skill as it was.")
    lines = [skills.render_step(st) for st in steps]
    if teach["mode"] == "replace":
        ops = [{"op": "replace_step", "id": sid, "step": lines[0]}] + [
            {"op": "insert_step", "after": sid, "step": l} for l in reversed(lines[1:])]
        what = f"do step {sid} like this"
    else:
        ops = [{"op": "insert_step", "after": sid, "step": l} for l in reversed(lines)]
        what = f"add this after step {sid}"
    run = session.state.get("skill_run")
    if run is not None:
        run["taught"] = True                    # one lesson per run
        if teach["mode"] == "replace":
            run["asked"] = sid                  # the user did that step themselves
    q = f"Change my '{name}' skill to {what}: {'; '.join(l[:60] for l in lines)}?"
    session.state["skill_pause"] = {"name": name, "question": q, "resume_at": teach["resume_at"], "input": teach.get("input", ""),
                            "teach": {"phase": "apply", "ops": ops}}
    return f"Paused my '{name}' skill to ask the user: {q}"


def _can_teach(s):
    """Can this skill run watch for the user taking over (and learn the step from them)?"""
    return (recorder.ENABLED and not session.state.get("remote") and s["risk"] != "read_only"
            and config.settings().get("skill_teach", True))


def _takeover_pause(s, run, sid, resume_at):
    """The user clicked or typed while the skill was working: stop and offer to learn from them."""
    mode = "replace" if any(f[0] == sid for f in run["failed"]) else "insert"
    q = (f"You took over after step {sid} of my '{s['name']}' skill. Want to show me how it should go? "
         "Say yes, do it yourself, then say 'done'.")
    session.state["skill_run"] = run
    session.state["skill_pause"] = {"name": s["name"], "question": q, "resume_at": resume_at, "input": run["input"],
                            "teach": {"phase": "show", "sid": sid, "mode": mode}}
    conversation.task_log(f"SKILL TAKE-OVER {s['name']} after {sid} ({mode})")
    session.think(f"✋ You took over after step {sid} - I'll stop and ask.")
    return f"Paused my '{s['name']}' skill: the user took over after step {sid}."


def _say_final(final, activity):
    session.state.pop("skill_final", None)
    session.state["messages"].append({"role": "assistant", "content": final})
    return {"reply": final, "activity": activity, "say": final}


FAILED_RE = re.compile(r"(i don'?t think i finished|\bi (couldn'?t|could not|wasn'?t able to)\b|didn'?t work|\bfailed\b|i'?m stuck)", re.I)


SAVE_TOOL = [{"name": "save_knowledge", "description":
              "Save what is worth remembering from this conversation. Pass empty lists if nothing is.",
              "input_schema": {"type": "object", "additionalProperties": False, "required": ["lessons", "profile"], "properties": {
                  "lessons": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                              "required": ["situation", "lesson", "kind"], "properties": {
                                  "situation": {"type": "string"}, "lesson": {"type": "string"},
                                  "kind": {"type": "string", "enum": ["mistake", "improvement", "preference"]}}}},
                  "profile": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                              "required": ["field", "value"], "properties": {
                                  "field": {"type": "string", "enum": list(knowledge.FIELDS)}, "value": {"type": "string"}}}}}}}]


REVIEW_PROMPT = promptfiles.load("review")


def _transcript(messages, limit=10):
    lines = []
    for m in messages[-limit:]:
        content = protocol.INFO_ECHO_RE.sub("", str(m.get("content") or "")).strip()
        if m["role"] == "user" and content.startswith(protocol.NOTE_START):
            lines.append("SYSTEM NOTE: " + content[:300])
        elif m["role"] == "user":
            lines.append("USER: " + content[:400])
        elif m["role"] == "tool" or content.startswith(protocol.TOOL_RESULT_START):
            lines.append("  RESULT: " + content[:350].replace("\n", " | "))
        else:
            for c in m.get("tool_calls") or []:
                fn = c.get("function", {})
                lines.append(f"  NOVA DID: {fn.get('name')} {json.dumps(fn.get('arguments'), ensure_ascii=False)[:160]}")
            if content:
                lines.append("NOVA: " + content[:400])
    return "\n".join(lines)


def why_review(user_text, out):
    """Should this turn be reviewed for the knowledge bank? Returns the reason, or None."""
    activity = out.get("activity") or []
    if {"Saved a lesson", "Updated your profile", "Remembered something"} & set(activity):
        return None                            # she already wrote it down herself
    clean_text = config._unspoken(user_text)
    if intents.CORRECTION.search(clean_text):
        return CORRECTED
    task = session.state.get("task") or {}
    if FAILED_RE.search(out.get("reply") or "") or (task.get("nudged") and task.get("done") is False):
        return "Nova could not finish what was asked"
    if intents.ABOUT_ME.search(clean_text):
        return "the user said something about themselves or how they want things done"
    return None


def review_turn(why):
    """Background: one brain call that turns the last exchange into lessons / profile facts."""
    def run():
        try:
            time.sleep(1.0)
            end = time.time() + 40
            while voice_io.mouth.busy() and time.time() < end:      # don't slow her answer down
                time.sleep(0.2)
            with session.lock:      # only to copy the conversation; the slow brain call below must not block the user
                talk = _transcript(list(session.state["messages"]))
            s = config.settings()
            model = config.brain(s)
            prompt = REVIEW_PROMPT.format(why=why, known=knowledge.recall()[:1500], talk=talk)
            _, text, calls = llm.chat(model, [{"role": "user", "content": prompt}], SAVE_TOOL, fallback=s["model"])
            data = next((c["args"] for c in calls if c["name"] == "save_knowledge"), None)
            if data is None:                               # a model without tool calling may answer with plain JSON
                m = re.search(r"\{.*\}", text or "", re.S)
                data = json.loads(m.group(0)) if m else {}
                data = data.get("args", data) if isinstance(data, dict) else {}
            saved = []
            for l in (data.get("lessons") or [])[:2]:
                if isinstance(l, dict) and l.get("lesson") and why == CORRECTED:      # 4f: asked first, not saved
                    pend = session.state.pop("lesson_q", None) or {}
                    qid = lessons.offer(l.get("situation", ""), l["lesson"], "you corrected me",
                                        replace=pend.get("id") if time.time() - pend.get("ts", 0) < LESSON_SECS else None)
                    if qid:
                        saved.append(f"lesson for the daily list (#{qid}): {l['lesson'][:120]}")
                elif isinstance(l, dict) and l.get("lesson"):
                    msg = knowledge.learn_lesson(l.get("situation", ""), l["lesson"], l.get("kind", "mistake"), source="auto")
                    if "saved" in msg or "already" in msg:
                        saved.append(f"lesson: {l['lesson'][:120]}")
            for f in (data.get("profile") or [])[:3]:
                if isinstance(f, dict) and f.get("value"):
                    if "Noted" in knowledge.update_profile(f.get("field", "facts"), f["value"]):
                        saved.append(f"profile {f.get('field')}: {f['value'][:80]}")
            conversation.task_log(f"LEARNED ({why}): " + ("; ".join(saved) if saved else "nothing worth saving"))
            if saved and not voice_io.mouth.busy():
                avatar_client.send("say", text="📝 Noted - I'll remember that.", secs=4, timeout=2)
                avatar_client.send("react", name="nod", timeout=2)
        except Exception as e:
            conversation.task_log(f"LEARNING failed: {e}")
    runtime.spawn("learning.run", run)


def after_turn(user_text, out):
    """Everything that happens once an answer is out: her body reacts, and she may learn from the exchange."""
    try:
        clean_text = config._unspoken(user_text)
        corrected = bool(intents.CORRECTION.search(clean_text))
        asked = session.state.pop("react_after", None)      # the brain's own pick (react tool) wins over the automatic one
        session.state.pop("invent", None)
        stage = out.get("stage_reaction") or shared.stage_reaction(shared.stage_directions(out.get("reply") or "")[1])
        if not asked and stage:      # she wrote '[react: "laugh"]' / '*spins*' instead of calling react: do it
            name = tools.reaction_name(stage)
            if name in REACTIONS or name in body_module.load_moves():
                conversation.task_log(f"STAGE DIRECTION without react -> playing {name}")
                asked = {"name": name, "repeat": None}
        if not asked:      # she still only *said* she's dancing (even after the nudge): make her words true
            claims = list(conversation.BODY_CLAIM_RE.finditer(out.get("reply") or ""))
            claim = bool(claims)
            word = next(((c.group("v") or c.group("w")).lower() for c in claims if c.group("v") or c.group("w")), "")
            name = {"danced": "dance", "clapped": "clap", "spun": "spin", "twirling": "spin"}.get(word) or body.BODY_WORDS.get(word)
            if claim and not name:     # "reacting now": the move the user asked for, if it's one she has
                name = next((body.BODY_WORDS[w] for w in re.findall(r"[a-z]+", clean_text.lower()) if w in body.BODY_WORDS), None)
            if name:
                conversation.task_log(f"BODY CLAIM without react -> playing {name}")
                asked = {"name": name, "repeat": None}
        if asked:      # asked to act something out ("no, dance properly!" is a correction AND a request)
            body.react_later(asked["name"], repeat=asked["repeat"])
        elif corrected:
            body.react_later("facepalm", wait_for_speech=False)
        else:
            body.react_later(body.pick_reaction(clean_text, out))
        why = why_review(user_text, out)
        if why:
            review_turn(why)
    except Exception as e:
        conversation.task_log(f"after_turn failed: {e}")


@api.app.get("/knowledge")
def knowledge_page():
    return send_from_directory(api.app.static_folder, "knowledge.html")


@api.app.get("/api/knowledge")
def knowledge_get():
    k = knowledge.load()
    facts = [{"id": f["id"], "text": learner.describe_fact(f), "confirmed": f.get("confirmed", False),
              "when": time.strftime("%d %b %Y", time.localtime(f.get("last", f.get("learned", 0))))}
             for f in sorted(learner.load_facts()["facts"], key=lambda f: -f.get("last", 0))]
    asks = [{"id": q["id"], "text": q["text"], "why": q["why"], "asked": q["asked"], "urgent": q["urgent"], "kind": q["kind"]}
            for q in questions.open_questions()]
    return jsonify(profile=k["profile"], lessons=sorted(k["lessons"], key=lambda l: -l.get("last", 0)),
                   labels=knowledge.LABELS, one=knowledge.ONE, many=knowledge.MANY, file=str(store.current().knowledge),
                   facts=facts, questions=asks, facts_file=str(store.current().facts), questions_file=str(store.current().questions))


@api.app.post("/api/knowledge")
def knowledge_edit():
    """The Knowledge page: add or remove profile entries and lessons."""
    b = api._body()
    do = b.get("do")
    if do == "profile_add":
        msg = knowledge.update_profile(api._text(b, "field"), api._text(b, "value"))
    elif do == "profile_remove":
        msg = knowledge.update_profile(api._text(b, "field"), api._text(b, "value") or "x", remove=True)
    elif do == "lesson_add":
        msg = knowledge.learn_lesson(api._text(b, "situation"), api._text(b, "lesson"), api._text(b, "kind", "preference"), source="user")
    elif do == "lesson_delete":
        if not isinstance(b.get("id"), int) or isinstance(b.get("id"), bool):
            return jsonify(error="id must be a number"), 400
        msg = f"Deleted {knowledge.delete_lesson(b['id'])} lesson(s)."
    elif do in ("fact_delete", "fact_confirm", "question_drop", "question_yes"):
        if not isinstance(b.get("id"), int) or isinstance(b.get("id"), bool):
            return jsonify(error="id must be a number"), 400
        if do == "fact_delete":
            msg = f"Forgot {learner.delete_fact(b['id'])} fact(s)."
        elif do == "fact_confirm":
            msg = "Marked as right." if learner.confirm_fact(b["id"]) else "That fact isn't there any more."
        elif do == "question_yes":         # 4f: the tick - a lesson is used only after this (or a yes to her question)
            q = questions.get(b["id"])
            if not q or q["status"] != "open":
                return jsonify(error="That question isn't there any more."), 404
            questions.answer(q["id"], "yes", "ticked on the page")
            msg = accept_question(q)
        else:
            msg = "Okay, she won't ask that." if questions.answer(b["id"], "dropped", "dropped on the page") else \
                "That question isn't there any more."
    else:
        return jsonify(error="unknown action"), 400
    return jsonify(ok=True, message=msg)


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import body, config, conversation, integrations, session, skillrun, voice_io  # noqa: E402
