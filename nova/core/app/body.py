"""3D Nova's body: moves asked for in words, and reactions after she has spoken."""


import json
import random
import re
import threading
import time

from nova.avatar import avatar_client
from nova.brain import llm
from nova.audio import sfx
from nova.actions import tools
from nova import protocol
from nova.shared import REACTIONS


BODY_WORDS = {"dance": "dance", "dancing": "dance", "laugh": "laugh", "laughing": "laugh", "clap": "clap",
              "clapping": "clap", "applaud": "clap", "spin": "spin", "spinning": "spin", "twirl": "spin",
              "wave": "wave", "waving": "wave", "bow": "bow", "bowing": "bow", "nod": "nod", "yawn": "yawn",
              "facepalm": "facepalm", "shrug": "confused", "jump": "happy", "jumping": "happy", "cheer": "happy",
              "cry": "sad", "crying": "sad", "giggle": "laugh", "hug": "love", "blush": "love", "gasp": "surprised",
              "stretch": "yawn"}


STYLE_WORDS = r"\b(desi|indian|bollywood|punjabi|funky|crazy|silly|fancy|wild|cute|funny|cool|gangnam|hip hop)\b"


DANCE_NAMES = ["thumka", "bhangra", "garba", "dandiya", "kathak", "ghoomar", "lavani", "nagin", "robot", "moonwalk",
               "macarena", "twerk", "floss", "breakdance", "salsa", "tango", "waltz", "ballet", "disco", "bollywood"]


DANCE_WORDS = r"(dance|dancing|move|moves|step|steps|thumka|jig|twerk|moonwalk|pose|routine|groove|nachna|naach|nach)"


_NAME = r"(?:hey |hi |ok |okay )?(?:nova|novah|noah|nover|nava|nowa|sophia)"


_BODY_POLITE = re.compile(rf"^\W*(?:{_NAME}\W+)?(?:(?:no|nope|nah|arey|arre|yaar|oh)\W+)*|(?:\W+{_NAME})?\W*$", re.I)


BODY_ASK_RE = re.compile(
    r"^(?:(?:can|could|will|would) you |please |now |just |go |ok |okay |come on |show me |let'?s see you |"
    r"i want (?:to see )?you to |i'?d like you to |do you know how to )*"
    r"(?:(?P<verb>do|give me|show me|perform|try) (?:a |an |the |some |your |my |that |one |another |me a )?"
    r"(?:little |quick |small |cute |nice |happy |big |proper |real |usual |normal |regular |own )?)?"
    r"(?P<what>[a-z][a-z' -]{1,30}?)"
    r"(?:\s+(?:for me|for us|again|once more|now|please|one more time|properly|for real|a bit|"
    r"(?P<times>two|three|four|five|\d) times|twice))*\W*$", re.I)


BODY_NOT_DONE_RE = re.compile(
    r"\b(?:you'?re not|you are not|you aren'?t|you'?re still not|you are still not|you never)\s+(?P<a>\w+ing)\b|"
    r"\byou (?:didn'?t|did not|don'?t|do not|haven'?t)\s+(?P<b>dance|clap|spin|laugh|wave|bow|jump)\b|"
    r"\b(?:i )?(?:can'?t|cannot|don'?t) see you\s+(?P<c>\w+ing)\b", re.I)


_TIMES = {"two": 2, "twice": 2, "three": 3, "four": 4, "five": 5}


def _known_move(what):
    """'dance' / 'bhangra' / 'star jump' -> the name react plays, or None."""
    from nova.avatar import body
    what = what.lower().strip()
    if what in BODY_WORDS:
        return BODY_WORDS[what]
    slug = re.sub(r"[^a-z0-9]+", "_", what).strip("_")
    moves = body.load_moves()
    if slug in moves:
        return slug
    for m in moves:                       # "bhangra" / "bhangra dance" -> bhangra_bounce, "jumping jack" -> jumping_jacks
        stem = m.split("_")[0]
        if len(stem) >= 5 and (slug.startswith(stem) or m.startswith(slug)):
            return m
    return None


def body_command(text):
    """A plain 'dance' / 'clap for me' / 'you're not dancing' -> the answer dict, played after the short reply.
    A dance she doesn't know ('do a desi thumka') -> she asks what to do. Anything else -> None (the brain)."""
    if session.state.get("remote"):
        return None
    t = _BODY_POLITE.sub("", config._unspoken(text).strip()).strip()
    if not t or len(t.split()) > 9:
        return None
    asked = session.state.pop("body_ask", None)       # she asked "make one up, or my usual dance?" - this is the answer
    if asked and time.time() - asked["at"] < 180:
        if re.search(r"\b(make|invent|create|new one|try|learn|yes|yeah|yep|sure|ok(ay)?|go ahead)\b", t, re.I) \
                and not re.search(r"\b(usual|normal|regular|old|known|your own)\b", t, re.I):
            task = session.state.get("task")
            if task and task.get("tools") and "new_move" not in task["tools"]:
                task["tools"] = list(task["tools"]) + ["new_move"]
            session.state["messages"][-1]["content"] += (f"\n{protocol.NOTE_START}: they want you to invent the move "
                                                 f"'{asked['what']}' now with new_move, then say one short line]")
            session.state["invent"] = asked["what"]
            try:        # designing the poses takes the brain ~20 s: say something at once instead of going quiet
                voice_io.mouth.say(random.choice(("Okay, let me think of one.", "Alright, give me a moment to make it up.")))
            except Exception:
                pass    # intentional: the spoken heads-up is optional; the brain still designs the move
            return None                       # the brain designs the move
        if re.search(r"\b(usual|normal|regular|old|known|dance|that one|the one you know)\b", t, re.I):
            t = "dance"
    name, times, reply = None, None, None
    miss = BODY_NOT_DONE_RE.search(t)
    if miss:
        word = (miss.group("a") or miss.group("b") or miss.group("c") or "").lower()
        name = BODY_WORDS.get(word)
        if name:
            reply = f"Sorry! Here's a real {name} for you." if name == "dance" else "Sorry! Here it is, for real."
    if not name:
        m = BODY_ASK_RE.match(t)
        if not m:
            return None
        what = m.group("what").strip()
        times = _TIMES.get((m.group("times") or "").lower()) or (int(m.group("times")) if (m.group("times") or "").isdigit()
                                                                  else (2 if re.search(r"\btwice\b", t, re.I) else None))
        name = _known_move(what)
        if not name:
            import difflib
            words = what.lower().split()
            looks_like_move = (
                re.fullmatch(rf"(?:[a-z']+ ){{0,3}}{DANCE_WORDS}", what, re.I)      # "robot dance", "thumka"
                or (len(words) <= 3 and re.search(STYLE_WORDS, what, re.I))          # "desi dunca" (misheard "thumka")
                or any(difflib.get_close_matches(w, DANCE_NAMES, 1, 0.7) for w in words))   # "bangra", "roboto"
            if not (m.group("verb") and looks_like_move):
                return None                  # not "do a <something> dance" ("play dance songs"): the brain handles it
            reply = (f"I don't know a {what} yet. Should I make one up, or do my usual dance?")
            session.state["body_ask"] = {"what": what, "at": time.time()}
            conversation.task_log(f"BODY ASK: unknown move '{what}'")
            return {"reply": reply, "activity": [], "say": reply}
        reply = random.choice(("Sure, watch this!", "Here goes!", "Okay, watch me!"))
    if not avatar_client.running():
        reply = "I can't move right now: my 3D body isn't on the screen. Turn it back on from the tray menu."
        return {"reply": reply, "activity": [], "say": reply}
    session.state["react_after"] = {"name": name, "repeat": times}
    conversation.task_log(f"BODY COMMAND: {name}" + (f" x{times}" if times else ""))
    return {"reply": reply, "activity": [session.FRIENDLY["react"]], "say": reply}


def look_first(text, activity):
    """Small models sometimes 'answer' about the user or the screen without looking. If the user clearly asked
    Nova to look, take the picture first and hand her the result, so she describes what's really there.
    (If camera/screen viewing is switched off, the tool itself tells Nova so - no made-up answers either way.)
    Clear video-player requests (full screen, captions...) are done the same way."""
    watched = learning.watch_command(text)       # 👀 "watch me" / "done": code decides, never the brain
    if watched:
        return watched
    moved = body_command(text)          # 💃 "dance!" - code plays it, the brain can't pretend it did
    if moved:
        activity.extend(moved["activity"])
        return moved
    text = config._unspoken(text)       # only the user's words: the 📱 phone note itself mentions "the screen"
    video_action = next((a for rx, a in voice_io.VIDEO_ASK if rx.search(text)), None)
    if video_action and tools._video_window():
        name, args = "video_control", {"action": video_action}
    elif voice_io.SELECTION_ASK.search(text):     # before SCREEN_ASK: a screenshot only showed her one line of the selection
        name, args = "read_selection", {}
    elif voice_io.SCREEN_ASK.search(text):
        name, args = "look_at_screen", {"question": text}
    elif voice_io.CAMERA_ASK.search(text):
        name, args = "look_through_camera", {"question": text}
    else:
        # Plain facts, in English or Hinglish. The small model sometimes answers these from its head ("12:45 baje",
        # "Speed 10 Mbps"); fetching the fact first makes the answer right, and saves one round trip to the model.
        name, args = next(((tool, {}) for rx, tool in voice_io.FACT_ASK if rx.search(text)), (None, None))
        if not name:
            return
    session.think({"look_at_screen": "👀 You asked about your screen - I'll look at it first.",
           "look_through_camera": "📷 You want me to see you - I'll look through the camera first.",
           "video_control": "🎬 That's a video-player button - pressing it directly.",
           "read_selection": "📋 You selected some text - I'll copy it first."}.get(
        name, "📌 That's a plain fact - I'll check it instead of guessing."))
    model = config.brain()
    call = {"id": "look_0", "name": name, "args": args}
    session.state["messages"].append({"role": "assistant", "content": "",
                              "tool_calls": [{"function": {"name": call["name"], "arguments": call["args"]}}]}
                             if llm.supports_tools(model) else
                             {"role": "assistant", "content": json.dumps({"tool": call["name"], "args": call["args"]})})
    session.state["messages"].extend(judging.execute([call], True, activity, model))
    out = session.state["last_outs"][0]
    if name == "read_selection" and not voice_io.READ_MORE_ASK.search(config._unspoken(text)) and not out.startswith(("Error", "Nothing is")):
        # Just "read it": say the text itself. Through the brain it came out as one line (a summary, or the local
        # model's short reply limit), so the words are read out word for word here instead.
        return {"reply": out, "activity": activity, "say": out.split("\n[... cut here")[0]}


def _cap_history():
    """Drop the oldest messages, keeping the list starting at a real user message (so tool results keep their calls)."""
    msgs = session.state["messages"]
    if len(msgs) <= config.MAX_STORED:
        return
    cut = len(msgs) - config.MAX_STORED
    while cut < len(msgs) and not conversation._is_real_user(msgs[cut]):
        cut += 1
    del msgs[:cut]
    task = session.state.get("task")
    if task:
        task["start"] = max(0, task["start"] - cut)


def answer(text, quiet=False, remote=False):
    """Run one user message through Nova. Returns (result dict, http status).
    quiet: don't say the answer out loud (📱 phone requests - the user isn't at the laptop).
    remote: the request came from the phone, so every action that changes something asks first (is_risky)."""
    voice_io.note_task()
    with session.lock:
        conversation.stop_flag.clear()           # a Stop pressed for the last request doesn't cancel this one
        session.state["remote"] = remote
        session.state.pop("sleep_after", None)
        session.state.pop("invent", None)
        _cap_history()
        n, old_pending = len(session.state["messages"]), session.state["pending"]
        activity = []
        if session.state["pending"]:  # user moved on without answering: treat as declined
            session.state["messages"].extend(judging.execute(session.state["pending"], False, activity, config.brain()))
            session.state["pending"] = None
        try:  # tell the model what's on screen right now, so "search for X" happens in the app the user is in
            screen = tools.open_windows_summary()
        except Exception:
            screen = ""
        missing = conversation.not_open_note(text)
        screen = (screen or "none") + missing if missing else screen
        resume = conversation.new_task(text)
        # (Tried also passing the time and battery here to save a tool call: the small model then read the line
        # out loud and stopped calling tools for other facts. Only the window list is passed.)
        session.state["messages"].append({"role": "user", "content":
                                  text + resume + (protocol.windows_note(screen) if screen else "")})
        try:
            out = look_first(text, activity)
            if out:     # done without the brain (reading out selected text)
                session.state["messages"].append({"role": "assistant", "content": out["reply"]})
                voice_io.nap["last_task"] = time.time()
            else:
                out = conversation.agent_loop(activity)
        except Exception as e:
            del session.state["messages"][n:]  # roll back so the history stays valid
            session.state["pending"] = old_pending
            session.state.pop("task", None)
            session.state.pop("images", None)
            session.think(f"💥 Something went wrong: {e}", cloud_too=False)
            session.thoughts_done()
            if isinstance(e, llm.OllamaError):
                return {"error": str(e)}, 503
            conversation.task_log(f"ERROR in answer: {type(e).__name__}: {e}")
            return {"error": f"Something went wrong inside Nova ({type(e).__name__}: {e})."}, 500
        _cap_history()
    session.thoughts_done()
    if not (quiet or remote or out.get("pending") or out.get("stopped") or session.state.get("sleep_after")):
        asked = learning.question_line()
        if asked:                            # the daily list: asked at the laptop, after her answer, one at a time
            out["reply"] = (out.get("reply") or "").rstrip() + "\n\n" + asked
            out["say"] = ((out.get("say") or "").rstrip() + " " + asked).strip()
    if quiet:
        out.pop("say", None)
    else:
        voice_io.speak(out.pop("say", out.get("reply")))
        voice_io.bubble(out.get("reply"))
    learning.after_turn(text, out)
    if session.state.pop("sleep_after", False):     # her brain understood "go to sleep" (go_to_sleep tool): now really sleep
        voice_io.go_to_sleep("asked", quiet=True)
    return out, 200


REACT_RULES = [   # (where to look, pattern, reaction) - first match wins
    ("user", r"\b(love you|you'?re the best|you are the best|so sweet)\b", "love"),
    ("user", r"\b(thank(s| you)|thanku|shukriya|dhanyavaad)\b", "bow"),
    ("user", r"\b(good (job|girl|work)|well done|great job|nice (one|work|job)|awesome|shabash|perfect|brilliant)\b", "happy"),
    ("reply", r"\b(my mistake|my bad|oops|i messed up|i got (it|that) wrong)\b", "facepalm"),
    ("reply", r"(i don'?t think i finished|\bi (couldn'?t|could not|can'?t|cannot|wasn'?t able)\b|\bsorry\b|didn'?t work|\bfailed\b)", "sad"),
    ("reply", r"(didn'?t (quite )?catch|not sure what you mean|could you (repeat|say that again)|what do you mean|🤔|❓)", "confused"),
    ("user", r"\b(joke|make me laugh|something funny)\b", "laugh"),
    ("reply", r"(😂|🤣|😆|😹|\bhaha|\bhehe|\blol\b)", "laugh"),
    ("user", r"\b(ha ?ha|lol|lmao|so funny|hilarious)\b", "laugh"),
    ("reply", r"(❤|🫶|😍|🥰|💕|💖)", "love"),
    ("reply", r"(😮|😲|🤯|😱|\bwhoa\b|\bwow\b|no way)", "surprised"),
    ("reply", r"(🎉|🥳|🎊|💃|🙌)", "happy"),
    ("user", r"\b(good ?night|i'?m (so )?(tired|sleepy))\b", "yawn"),
    ("reply", r"^\W*(yes|yep|sure|of course|absolutely|haan)\b", "nod"),
    ("reply", r"^\W*(no|nope|nahi)\b[,.! ]", "shake"),
]


REACT_RULES = [(w, re.compile(rx, re.I), r) for w, rx, r in REACT_RULES]


def pick_reaction(user_text, out):
    """Which reaction fits this exchange, or None."""
    activity = out.get("activity") or []
    if "Acted it out" in activity:            # she already did one on request
        return None
    if out.get("error"):
        return "sad"
    reply = out.get("reply") or ""
    for where, rx, reaction in REACT_RULES:
        if rx.search(user_text if where == "user" else reply):
            return reaction
    done_steps = [a for a in activity if a in {session.FRIENDLY.get(t) for t in conversation.ACT_TOOLS}]
    if len(done_steps) >= 2 and not out.get("pending"):
        return "clap"                         # a multi-step task went through
    return None


def queue_reaction(args, activity):
    """The react tool during a chat: don't act it out now (she hasn't said anything yet) - after_turn plays it the
    moment she finishes speaking, so a laugh comes after the joke."""
    from nova.avatar import body
    name = tools.reaction_name(args.get("reaction"))
    if name not in REACTIONS and name not in body.load_moves():
        return (f"I can't do '{args.get('reaction')}' yet. I can: {tools.can_do()}. "
                "To do something new, invent it with new_move.")
    try:
        times = int(args.get("times") or 0) or None
    except (TypeError, ValueError):
        times = None
    session.state["react_after"] = {"name": name, "repeat": times}
    activity.append(session.FRIENDLY["react"])
    return (f"OK: you'll {name} right after you finish speaking this answer. Now give your answer; "
            f"don't say you already did it.")


def queue_new_move(args, activity):
    """The new_move tool during a chat: check and learn the move now, but act it out only after she has spoken -
    like queue_reaction. (Played at once, the move came before her answer: "check this out!" after the dance.)"""
    from nova.avatar import body
    move, err = body.clean_move(args.get("name"), args.get("duration"), args.get("keys"))
    if err:
        return f"Error in new_move: {err}"
    snd, err = body.clean_sound(args.get("sound"))
    if err:
        return f"Error in new_move: sound: {err}"
    st = avatar_client.status() or {}
    if not st.get("live"):
        return ("I can't do new moves right now: my live 3D body is off (tray menu: Live 3D Nova), so only my "
                "built-in reactions work.")
    note = ""
    if snd:      # made now, once: a word in her own voice (~0.5 s to make) or a synthesized little piece
        neural = getattr(voice_io.mouth, "neural", None)
        try:
            made = sfx.save_move_sound(move["name"], snd, say=neural.synth if neural else None)
        except Exception as e:
            made = None
            conversation.task_log(f"MOVE SOUND failed: {e}")
        if made:
            move["sound"] = snd
        else:
            note = " (Its sound couldn't be made - my voice engine isn't loaded - so it's silent.)"
    avatar_client.send("move", move=move, play=False, timeout=3)
    if args.get("save", True) is not False:
        body.save_move(move)
    try:
        times = int(args.get("times") or 0) or None
    except (TypeError, ValueError):
        times = None
    session.state["react_after"] = {"name": move["name"], "repeat": times}
    activity.append(session.FRIENDLY["new_move"])
    return (f"OK: your new move '{move['name']}' is saved and you'll do it right after you finish speaking this "
            f"answer.{note} Now give your answer; don't say you already did it.")


def react_later(reaction, wait_for_speech=True, repeat=None):
    """Play a reaction the moment she has finished speaking - a laugh right after the joke's punchline, not before."""
    if not reaction:
        return

    def run():
        if wait_for_speech:
            time.sleep(0.2)                      # speak() has queued the answer by now
            end = time.time() + 60
            while voice_io.mouth.talking() and time.time() < end:
                time.sleep(0.05)
        if not voice_io.mouth.talking() or not wait_for_speech:
            out = avatar_client.send("react", name=reaction, repeat=repeat, timeout=2)
            if out and out.get("played"):
                voice_io.mouth.hold(sfx.length(reaction) + 0.3)     # its sound effect: keep the mic from hearing it
            conversation.task_log(f"REACTION: {reaction}")
    threading.Thread(target=run, daemon=True).start()


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import config, conversation, judging, learning, session, voice_io  # noqa: E402
