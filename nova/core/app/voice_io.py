"""Her voice and ears: speaking, the ear bubble, wake word and conversation mode, sleep, barge-in, naps."""


import threading
import time

from nova import runtime
from nova.avatar import avatar_client
from nova.avatar import body as body_module
from nova.core import characters
from nova.actions import coworker
from nova.brain import llm
from nova.brain import retrieval
from nova.actions import skills
from nova.brain import toolpick
from nova.audio import voice
from nova import intents
from nova import protocol
from nova.log import log_exception
from nova.core.app import config, conversation, eventbus, voice_machine


mouth = voice.Mouth()


# R10 shadow mode: one state machine next to the flags below (convo, nap, listen_state). Nothing reads it yet; every
# event is checked against the flags and a disagreement is logged (voice_mismatch). See voice_machine.py.
machine = voice_machine.VoiceState(publish=eventbus.publish)


def _shadow(event):
    """Tell the shadow machine, then compare it with the flags. Never lets the voice side fail."""
    try:
        machine.fire(event)
        machine.check({"ears_off": listen_state.get("ears_off", False), "convo_on": convo["on"],
                     "asleep": nap["asleep"], "deep": nap.get("deep", False), "thinking": convo["thinking"]})
    except Exception:
        log_exception("voice_state shadow")


def _lips(on):
    eventbus.publish("speak_start" if on else "speak_end")    # the chat page's talking face
    _shadow("reply_started" if on else "reply_done")


mouth.on_lips = _lips


def ears_changed(status, heard):
    """The ears' status line or what they last heard changed: the chat page's 👂 line (hands-free)."""
    eventbus.publish("ears", {"status": status, "heard": heard[-1:]})


voice.wake.on_change = ears_changed        # its home module: both ears classes inherit _EarsBase from wake.py


def use_character_voice_and_name(ch):
    """Her character's voice, and the name she answers to ("Hey Lucy" wakes Lucy, not "Hey Nova")."""
    global JUST_STOP_RE
    voice.speak.NEURAL_VOICE = ch["voice"]        # its home module: speak.py reads it when she talks
    voice.set_wake(characters.wake_names(ch), ch["wake"]["hint"], ch["name"])
    JUST_STOP_RE = intents.just_stop_re(voice.NOVA_NAMES)


def apply_character(cid):
    """Switch to another character now: her voice at once, her 3D look (live 3D), her personality from the next
    request on. -> the character."""
    ch = characters.get(cid)
    use_character_voice_and_name(ch)
    if convo["on"]:      # an open conversation belonged to the old character: the new one waits for her own name
        convo["on"] = False
        listen_state["single"] = False
        ear_close()
        _shadow("conversation_over")
    conversation.task_log(f"CHARACTER {ch['name']}: voice {ch['voice']}, wake word \"Hey {ch['name']}\"")
    eventbus.publish("wake_phrase", {"phrase": prompts.wake_phrase()})
    with session.lock:                # a fresh chat: the small brain copies its old name and tone from the history otherwise
        session.state["messages"].clear()
        session.state["pending"] = None
    resume.clear_chat()
    if mouth.voice_name and mouth.voice_name.startswith("Kokoro"):
        mouth.voice_name = f"Kokoro {ch['voice']} (natural voice)"
    try:
        look, _ = body_module.change_look({}, ch["look_preset"])
        avatar_client.send("look", look=look, timeout=3)
    except Exception as e:
        conversation.task_log(f"CHARACTER look not applied: {e}")
    return ch


events = []  # things that happened by voice, for the chat window to show


def push_event(e):
    events.append(e)
    del events[:-config.MAX_EVENTS]     # recent ones, for the tests and the task log; the pages get them from the bus
    eventbus.publish("event", e)


mouth.on_start = lambda words, timing="": conversation.task_log(f"VOICE starts: {words[:60]}  ({timing})")   # "she's slow / out of sync"


mouth.on_gap = lambda secs, who, words="": conversation.task_log(f"VOICE PAUSE {secs:.1f}s mid-reply, waiting for the {who} "
                                                    f"(before: {words[:40]})")   # brain = text late, voice = synth slow


def speak(text):
    if config.settings()["voice"] and text:
        mouth.say(text)      # each sentence also appears in 3D Nova's bubble while it is being said (voice.Mouth)


def bubble(text, secs=None):
    """Show text in 3D Nova's bubble - only when her voice is off; when it's on, speaking it shows it in step."""
    text = (text or "").strip()
    if text and not config.settings()["voice"]:
        avatar_client.send("say", text=text, secs=secs or min(14, 3 + len(text) / 15), timeout=2)


def announce(text, spoken=None):
    """Tell the user something: said out loud (with matching bubbles), or as one bubble when the voice is off."""
    speak(spoken or text)
    bubble(text)


def warm_brain():
    """Run the long system prompt through the model once at start-up, so the first real question isn't the slow one.
    Also loads the small model that picks facts and tools for each request (CPU, a few seconds)."""
    retrieval.load_in_background()
    try:
        model = config.settings()["model"]
        llm._post("/api/chat", {"model": model, "stream": False, "keep_alive": llm.KEEP_ALIVE,
                                "messages": [{"role": "system", "content": prompts.system_prompt(model)},
                                             {"role": "user", "content": "hi"}],
                                "tools": llm._to_ollama_tools(toolpick.schemas(toolpick.pick("hi")))
                                if llm.supports_tools(model) else None,
                                "options": {"num_ctx": llm.NUM_CTX, "num_predict": 1}}, timeout=300)
    except Exception:
        log_exception("warm-up")             # the first real question will just be the slow one


convo = {"on": False, "last_heard": 0.0, "thinking": False}


ear_cmds = []                         # commands for the ear bubble


ear_hooks = {"show": None, "hide": None}   # set by desktop.py


SLEEP_MAX_WORDS = 8


JUST_STOP_RE = intents.just_stop_re(voice.NOVA_NAMES)


use_character_voice_and_name(characters.get(config.settings().get("character")))   # her name, voice and "stop" words


def just_stop(text):
    return bool(JUST_STOP_RE.match(config._unspoken(text)))


def stopped_by_voice(text, via="silent"):
    """'Nova, stop' while she's quiet (or right after she was talked over): stop everything, say nothing."""
    was_busy = conversation.stop_now()
    conversation.task_log(f"VOICE STOP: {text[:40]!r} ({'stopped a task' if was_busy else 'nothing was running'})")
    avatar_client.send("say", text="🤐 Okay.", secs=2, timeout=2)
    push_event({"reply": "🤐 Okay, stopped." if was_busy else "🤐 Okay.", "kind": "suggestion"})
    if via != "silent":
        ear_listen_after_speech() if convo["on"] else ear_close()


def wants_sleep(text):
    """Is this the user telling her to go to sleep (not just a sentence that mentions sleep)?"""
    clean = config._unspoken(text)
    return bool(intents.SLEEP.search(clean)) and len(clean.split()) <= SLEEP_MAX_WORDS


def ear_listen():
    if ear_hooks["show"]:
        ear_hooks["show"]()
    ear_cmds.append({"do": "listen"})
    del ear_cmds[:-10]
    eventbus.publish("ear", {"do": "listen"})


def ear_listen_after_speech():
    """Wait until Nova finishes talking, then listen again."""
    def run():
        time.sleep(0.4)
        while mouth.busy():
            time.sleep(0.2)
        ear_listen()
    runtime.spawn("voice_io.run", run)


def ear_close():
    ear_cmds.append({"do": "stop"})
    del ear_cmds[:-10]
    eventbus.publish("ear", {"do": "stop"})
    if ear_hooks["hide"]:
        ear_hooks["hide"]()


# Silent listening: Windows' modern recognizer in the background - no windows at all. The ear bubble (Windows voice
# typing) is only a fallback if that recognizer is unavailable (Online speech recognition off) or keeps failing.
# Whisper (offline, most accurate) is used when installed; Windows' recognizer is the backup.
windows_transcriber = voice.Transcriber()


whisper = voice.WhisperTranscriber() if voice.WhisperTranscriber.available() else None


LISTEN_PAUSE = (0.3, 2.0)       # ⚙ Senses: seconds of quiet that mean you've finished speaking


LISTEN_MAX = (4, 30)            # ⚙ Senses: the longest one spoken request may be


MIC_LEVEL = (1, 10)             # ⚙ Senses: how loud you must be before she starts recording (5 = the built-in levels)


def apply_listening(s=None):
    """Her ears' timing from the settings (Whisper only - the Windows recognizer decides for itself)."""
    s = s or config.settings()
    if whisper:
        whisper.END_PAUSE = min(max(float(s.get("listen_pause", 0.7)), LISTEN_PAUSE[0]), LISTEN_PAUSE[1])
        whisper.MAX_LEN = float(min(max(int(s.get("listen_max", 10)), LISTEN_MAX[0]), LISTEN_MAX[1]))
        try:
            level = min(max(float(s.get("mic_level", 5)), MIC_LEVEL[0]), MIC_LEVEL[1])
        except (TypeError, ValueError):
            level = 5
        whisper.scale = voice.WhisperTranscriber.level_scale(level)


apply_listening()


transcriber = whisper or windows_transcriber


if whisper:
    # The words so far show in 3D Nova's bubble while the user is still speaking (the final reading replaces them).
    whisper.on_partial = lambda t: avatar_client.send("say", text=f"“{t[:150]}…”", secs=3, timeout=1)
    whisper.self_voice = mouth.busy       # never take her own voice (answers, notifications, reaction sounds) as a request
    whisper.expect_reply = lambda: expecting_reply()


listen_state = {"loop": False, "rt_broken": False, "single": False}


if whisper:
    runtime.spawn("voice_io.whisper_load", lambda: whisper.load())   # warm up so the first request is quick


def set_ears(on):
    """👂 in the chat. Off = she doesn't listen at all: no "Hey Nova", and a conversation or a "yes?" she was
    waiting for stops listening too (a recording in progress is dropped). The 🎤 button still works - that's you
    choosing to talk. On = "Hey Nova" works again."""
    listen_state["ears_off"] = not on
    if whisper:
        whisper.muted = not on
    if on:
        if not nap.get("deep"):           # deep asleep: the ears come back when she's woken (wake_body)
            ears.start()
        avatar_client.send("thought", text="👂 ears on", secs=3, timeout=2)
        conversation.task_log("EARS ON (👂)")
        _shadow("ears_on")
        return
    ears.stop()
    convo["on"] = False
    listen_state["single"] = False
    ear_close()
    avatar_client.send("thought", text="🙉 ears off", secs=4, timeout=2)
    conversation.task_log("EARS OFF (👂) - not listening at all")
    _shadow("ears_off")


def silent_ok():
    if listen_state["rt_broken"]:
        return False
    return transcriber is whisper or voice.online_speech_on()


ASKED_SECS = 60      # a bare "yes"/"no" counts as an answer this long after she asked something


def expecting_reply():
    """Is she waiting for a yes/no? (An Allow/No question, "want to see it?", or her last answer was a question.)
    Only then is a short, slightly unclear "No." taken as an answer - otherwise it is most likely a cough."""
    return (bool(session.state["pending"]) or bool(integrations.offer["job"] and time.time() < integrations.offer["until"])
            or time.time() - convo.get("asked", 0.0) < ASKED_SECS)


def _still_listening():
    return convo["on"] or bool(session.state["pending"]) or listen_state["single"]


def ears_on():
    """Light up 3D Nova's ears while the microphone is taking in a request; returns the function that turns them
    off. The waves grow while a voice is coming in (transcriber.hearing_at). A heartbeat keeps them on for as long
    as listening lasts - the old "I'm listening" bubble showed for a fixed time and other bubbles replaced it."""
    done = threading.Event()

    def beat():
        last = None
        next_keepalive = 0.0
        while not done.is_set():
            hearing = time.time() - getattr(transcriber, "hearing_at", 0.0) < 0.4
            if hearing != last or time.time() >= next_keepalive:
                avatar_client.send("listen", on=True, hearing=hearing, secs=2.5, timeout=1)
                last, next_keepalive = hearing, time.time() + 1.0
            done.wait(0.15)
        avatar_client.send("listen", on=False, timeout=1)
    runtime.spawn("voice_io.beat", beat)
    _shadow("hearing_started")

    def stop():
        done.set()
        _shadow("hearing_ended")
    return stop


def silent_loop():
    global transcriber
    if listen_state["loop"]:
        return
    listen_state["loop"] = True
    try:
        while _still_listening() and not listen_state.get("ears_off"):
            time.sleep(0.3)
            while mouth.busy():           # never listen while she's talking
                time.sleep(0.2)
            if not _still_listening():
                break
            ears_off = ears_on()
            try:
                kind, text = transcriber.listen()
            finally:
                ears_off()
            if not _still_listening():    # she was sent to sleep while listening
                break
            if kind == "text":
                listen_state["single"] = False
                # (kept up until her own first spoken sentence replaces it - Mouth._bubble)
                avatar_client.send("say", text=f"“{text}”", secs=30, timeout=2)
                conversation.task_log(f"HEARD {text[:60]!r} ({getattr(transcriber, 'timing_note', lambda: '')()})")
                heard(text, via="silent")
            elif kind in ("silence", "unclear"):
                rejected = getattr(transcriber, "rejected", "")
                ears._note(f"ignored noise: {rejected}" if rejected else
                           "heard a sound but couldn't make out words" if kind == "unclear" else "listened, heard no speech")
                if listen_state["single"]:
                    listen_state["single"] = False
                    avatar_client.send("say", text=f"Sorry, I didn't catch that 🤔 Say “{prompts.wake_phrase()}” again.", secs=4, timeout=2)
                elif (convo["on"] and time.time() - max(convo["last_heard"], nap["last_task"]) > IDLE_NAP
                      and not session.lock.locked() and not mouth.busy() and not convo["thinking"]):
                    go_to_sleep("idle")     # never in the middle of a task or while she's talking
                elif kind == "unclear" and convo["on"] and time.time() - convo["last_heard"] < 60:
                    avatar_client.send("say", text="Hmm, say that again? 🤔", secs=3, timeout=2)
            else:
                ears._note(f"background listening failed ({text})")
                if transcriber.failures >= 2 and transcriber is whisper and voice.online_speech_on():
                    transcriber = windows_transcriber
                    ears._note("Whisper failed twice - switching to Windows' recognizer")
                elif transcriber.failures >= 2:
                    listen_state["rt_broken"] = True
                    ears._note("switching to the voice-typing bubble")
                    if ear_hooks["show"]:
                        ear_listen_after_speech()
                    break
    finally:
        listen_state["loop"] = False


def _start_conversation(greet=True):
    """From now on she keeps listening after every answer, until "Nova, go to sleep" or 5 minutes without a task."""
    if not convo["on"]:
        convo.update(on=True, last_heard=time.time())
        _shadow("wake_heard")
        if greet:
            name = characters.get(config.settings().get("character"))["name"]
            announce(f"I'm awake! 👂 Talk to me. Say “{name}, go to sleep” when you're done.", spoken="I'm awake! I'm listening.")


def on_wake(conversation=False):
    """'Hey Nova' (or 'Nova, wake up'): she wakes up and stays in the conversation until told to sleep."""
    mouth.stop()
    note_task()
    if silent_ok():
        if convo["on"] and listen_state["loop"]:
            return                        # already listening
        _start_conversation(greet=conversation)
        runtime.spawn("voice_io.silent_loop", silent_loop)
        return
    if ear_hooks["show"] is None:      # no desktop app (plain browser): fall back to the chat page
        if host.show_window_hook:
            host.show_window_hook()
        push_event({"listen": True})
        return
    if not convo["on"]:
        _start_conversation(greet=conversation)
        ear_listen_after_speech() if conversation else ear_listen()


def go_to_sleep(reason="asked", quiet=False):
    """End the conversation and nap in the bottom-right corner of the screen.
    reason: "asked" (the user said so), "idle" (5 minutes without a task), "quiet" (nobody talking).
    quiet: she has already said goodnight herself."""
    was_on = convo["on"]
    convo["on"] = False
    listen_state["single"] = False
    ear_close()
    conversation.task_log(f"SLEEP ({reason})")
    nap["reason"] = reason                  # "asked" = the user's choice: presence won't wake her (on_presence)
    if (reason == "asked" or was_on) and not quiet:
        line = (f"Okay, I'll take a nap in the corner. Say “{prompts.wake_phrase()}” when you need me 😴" if reason == "asked" else
                f"It's been quiet for a while, so I'll take a nap in the corner. Say “{prompts.wake_phrase()}” when you need me 😴")
        announce(line)
        sleep_body(after_words=True)      # the goodnight first, then she lies down
    else:
        sleep_body()
    _shadow("sleep_asked" if reason == "asked" else "nap_due")


def confirm_pending(approved, quiet=False, remote=None):
    """Answer the Allow / No question Nova asked (from the chat page, by voice, or 📱 from the phone)."""
    with session.lock:
        if not session.state["pending"]:
            return {"reply": "", "activity": []}, 200
        if remote is not None:
            session.state["remote"] = remote
        conversation.stop_flag.clear()
        nap["last_task"] = time.time()
        activity = []
        conversation.new_turn()
        session.think("👍 You said yes - going ahead." if approved else "👌 You said no - I'll leave it.")
        again = next((p for p in session.state["pending"] if (p["args"] or {}).get("resume_at") is not None), None)
        if again:                            # a skill asked its own question: carry it on (or stop it) here
            session.state["pending"] = None
            try:
                out = skillrun.resume_skill(again["args"], approved, activity)
            except Exception as e:
                session.think(f"💥 Something went wrong: {e}", cloud_too=False)
                session.thoughts_done()
                conversation.task_log(f"ERROR in skill resume: {type(e).__name__}: {e}")
                return {"error": f"Something went wrong inside Nova ({type(e).__name__}: {e})."}, 500
            session.thoughts_done()
            if quiet:
                out.pop("say", None)
            else:
                speak(out.pop("say", out.get("reply")))
                bubble(out.get("reply"))
            return out, 200
        if not approved:                 # "no" to a skill that only sounded right: that sentence never starts it again
            task = session.state.get("task") or {}
            for p in session.state["pending"]:
                if p["name"] == "run_skill" and task.get("skill_how") == "fuzzy" and task.get("skill") == (p["args"] or {}).get("name"):
                    skills.add_not_for(task["skill"], task.get("goal", ""))
                    conversation.task_log(f"SKILL DENIED {task['skill']}: '{task.get('goal', '')[:80]}' will not start it again")
        session.state["messages"].extend(judging.execute(session.state["pending"], approved, activity, config.brain()))
        session.state["pending"] = None
        n = len(session.state["messages"])
        try:
            out = conversation.agent_loop(activity)
        except Exception as e:
            del session.state["messages"][n:]  # the approved action ran; only the half-finished follow-up is dropped
            session.think(f"💥 Something went wrong: {e}", cloud_too=False)
            session.thoughts_done()
            if isinstance(e, llm.OllamaError):
                return {"error": str(e)}, 503
            conversation.task_log(f"ERROR in confirm: {type(e).__name__}: {e}")
            return {"error": f"Something went wrong inside Nova ({type(e).__name__}: {e})."}, 500
    session.thoughts_done()
    if quiet:
        out.pop("say", None)
    else:
        speak(out.pop("say", out.get("reply")))
        bubble(out.get("reply"))
    return out, 200


def heard(text, via="bubble"):
    """Something the user said by voice. Answers it; the bubble path also schedules the next listen
    (the silent loop schedules its own)."""
    text = text.strip()
    convo["last_heard"] = time.time()
    ears._note(f"heard: {text}"[:160])
    push_event({"me": text})            # in the chat at once - not only after her answer (that can take 20 s)
    if wants_sleep(text):
        go_to_sleep("asked")
        return
    if just_stop(text):
        stopped_by_voice(text, via)
        return
    note_task()
    if integrations.help_first():                   # "yes" right after "Notepad shows an error - want me to help?"
        helped = integrations.take_help(text)
        if helped is not None:
            push_event(dict(helped))
            if via != "silent":
                ear_listen_after_speech() if convo["on"] else ear_close()
            return
    said = learning.take_question(text)         # "yes" right after "save it as a skill?" (the daily list)
    if said is None:
        said = integrations.take_offer(text)        # "yes" right after "your email is ready - want to see it?"
    if said is not None:
        push_event({"reply": said})
        announce(said)
        if via != "silent":
            ear_listen_after_speech() if convo["on"] else ear_close()
        return
    convo["thinking"] = True
    _shadow("request_heard")
    eventbus.publish("thinking", {"on": True})
    avatar_client.send("anim", name="think", timeout=2)
    try:
        if session.state["pending"] and (intents.YES.search(text) or intents.NO.search(text)):
            out, status = confirm_pending(bool(intents.YES.search(text)))
        else:
            out, status = body.answer(f"{protocol.SPOKEN} {text}")
    finally:
        convo["thinking"] = False
        _shadow("answered")
        eventbus.publish("thinking", {"on": False})
    convo["asked"] = time.time() if str(out.get("say") or out.get("reply") or "").rstrip().endswith("?") else 0.0
    if not (out.get("say") or out.get("reply")) and not mouth.busy():
        avatar_client.send("say", text="", timeout=2)       # nothing was said back: take her "heard" bubble down
    push_event(dict(out))             # shows up in the chat page's history too (what was said went in at the top)
    ears._note(f"answered: {out.get('reply') or out.get('error', '')}"[:160])
    if status != 200:     # (the reply itself was already said and shown by answer())
        announce("Sorry, my brain isn't answering. Is Ollama running?")
    if out.get("pending"):
        what = ", ".join(p["label"].lower() for p in out["pending"])
        announce(f"May I {what}? Say “yes” or “no” 🙋", spoken=f"Should I {what}? Say yes or no.")
    if via == "silent":
        return
    if convo["on"] or session.state["pending"]:
        ear_listen_after_speech()
    else:
        ear_close()


def on_voice(text):
    """Fallback path: the listener transcribed the request itself (no desktop app)."""
    heard(text)


def on_request(text):
    """'Hey Nova, <request>' said in one breath (Whisper heard both). Starts a conversation, like 'Hey Nova' alone."""
    mouth.stop()
    note_task()
    _start_conversation(greet=False)
    heard(text, via="silent")
    if _still_listening():            # she asked "yes or no?", or we're in a conversation
        runtime.spawn("voice_io.silent_loop", silent_loop)


# With Whisper installed, it does all the listening (wake word and request). Otherwise Windows' recognizer does.
if whisper:
    ears = voice.WhisperEars(mouth, on_request, whisper)
    ears.paused = lambda: listen_state["loop"] or convo["thinking"]
else:
    ears = voice.Ears(mouth, on_voice)


ears.on_wake = on_wake


ears.log = conversation.task_log


ears.on_name = lambda: wake_body()      # (defined below)


ears.on_sleep = lambda: go_to_sleep("asked")


ears.is_stop = lambda text: just_stop(text)            # "Hey Nova, stop": stop, don't open a conversation


ears.on_stop = lambda text: stopped_by_voice(text)


# ("Nova, stop", "wait", "ruko"). voice.BargeIn tells the user's voice from her own echo. It only runs while her ears
# are on (👂 Hey Nova), her voice is on and ⚙ "✋ Let me interrupt her by voice" is ticked: it opens the microphone
# only while she is talking. The setting is cached here, since BargeIn asks ~30 times a second.
barge_flags = {}


def refresh_barge(s=None):
    s = s or config.settings()
    barge_flags.update(on=bool(s.get("barge_in", True)) and bool(s.get("voice", True)) and bool(s.get("wake")))


def barge_enabled():
    return (barge_flags.get("on", False) and not listen_state.get("ears_off") and not nap.get("deep")
            and not (whisper and whisper.muted))


def on_barge(kind, text):
    """The user talked over her. kind "stop" (stop / ruko / bas...) also stops a task she is working on; "name"
    (her name, "wait", "hold on") and "talk" (a sentence of their own) only stop her talking. Then she listens
    for what comes next."""
    if kind == "stop" and session.lock.locked():
        conversation.stop_now()
    t_stop = time.time()
    mouth.stop(quick=True)
    runtime.spawn("resume.cut_off", resume.cut_off, args=(t_stop,))     # keep what she didn't get to say
    _shadow("barge_in")
    ears._note(f"you talked over me: “{text[:50]}” - stopped ({kind})")
    if whisper:
        whisper.next_floor = barge.floor       # the user is still talking: don't take their voice for the room's noise
    if kind in ("name", "talk") or convo["on"]:
        on_wake()                             # listen for the rest (starts a conversation, like "Hey Nova")
    else:
        avatar_client.send("say", text="🤐", secs=2, timeout=2)


barge = None


if whisper:
    refresh_barge()
    barge = voice.BargeIn(mouth, whisper, on_barge, enabled=barge_enabled)
    barge.log = conversation.task_log
    barge.start()


IDLE_NAP = 300


DEEP_SLEEP_AFTER = 600      # asleep this long (⚙ "Deep sleep" on): her mic turns off; only a click or a typed message wakes her


nap = {"asleep": False, "last_task": time.time()}


def sleep_body(after_words=False):
    """after_words: she has just been given a goodnight line - let her say it standing, then lie down."""
    if not nap["asleep"]:
        nap["since"] = time.time()          # the deep-sleep clock counts from when she lay down
    nap["asleep"] = True
    nap["gen"] = gen = nap.get("gen", 0) + 1
    if not after_words:
        avatar_client.send("sleep", on=True, timeout=3)
        return

    def later():
        give_up = time.time() + 25
        if config.settings()["voice"]:
            while mouth.talking() and time.time() < give_up:
                time.sleep(0.1)
        else:
            time.sleep(3)                 # (her bubble shows the line instead)
        if nap["asleep"] and nap.get("gen") == gen:     # not woken again in the meantime
            avatar_client.send("sleep", on=True, timeout=3)
    runtime.spawn("voice_io.later", later)


def deep_sleep_due(now=None):
    now = now or time.time()
    return (nap["asleep"] and not nap.get("deep") and config.settings().get("deep_sleep", True)
            and now - nap.get("since", now) >= DEEP_SLEEP_AFTER)


def go_deep():
    """Asleep for a while: turn her ears off completely (no mic, no Whisper) - the TV can talk all it likes.
    A click on her, a typed message or a phone message wakes her (wake_body turns the ears back on)."""
    nap["deep"] = True
    ears.stop()
    conversation.task_log("DEEP SLEEP - mic off until clicked or messaged")
    push_event({"reply": "💤 I'm deep asleep now - my mic is off. Click me (or type to me) to wake me up.", "kind": "suggestion"})
    avatar_client.send("sleep", on=True, deep=True, timeout=3)
    _shadow("deep_due")


def wake_body(clicked=False):
    was_asleep = nap["asleep"] or nap.get("deep")
    if nap.get("deep"):
        nap["deep"] = False
        if config.settings()["wake"]:
            ears.start()                     # her ears back on
            conversation.task_log("WOKE from deep sleep - mic on again")
    if nap["asleep"]:
        nap["asleep"] = False
        nap["reason"] = None
        nap["gen"] = nap.get("gen", 0) + 1
        avatar_client.send("sleep", on=False, clicked=clicked, timeout=3)
    if was_asleep:
        _shadow("woken")


def note_task():
    """The user gave her something to do (or woke her): the 5-minute nap clock starts again. It is also reset after
    every step of a task and when she finishes answering, so she naps only after 5 quiet minutes."""
    nap["last_task"] = time.time()
    wake_body()


def nap_due(now=None):
    now = now or time.time()
    return (not nap["asleep"] and now - nap["last_task"] >= IDLE_NAP and not session.lock.locked() and not mouth.busy()
            and not convo["thinking"] and not coworker.running())


def idle_watch():
    while True:
        time.sleep(10)
        try:
            if nap_due():
                go_to_sleep("idle")
            elif deep_sleep_due():
                go_deep()
            if learning.learn_due():
                learning.learn_now()
        except Exception as e:
            conversation.task_log(f"idle watch: {type(e).__name__}: {e}")


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import body, host, integrations, judging, learning, prompts, resume, session, skillrun  # noqa: E402
