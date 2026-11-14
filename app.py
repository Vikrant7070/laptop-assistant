"""Nova - a friendly assistant that lives in your laptop, powered by a local Ollama model.

Run:  python app.py   (opens http://127.0.0.1:5055)
"""
import json
import os
import re
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

import avatar_client
import cloud
import knowledge
import llm
import tools
import voice
from shared import host_allowed, ps_escape

ROOT = Path(__file__).parent
PORT = int(os.environ.get("PORT", 5055))
SETTINGS_FILE = tools.DATA / "settings.json"
DEFAULT_MODEL = "qwen3-vl:4b-instruct"  # fits a 6 GB laptop GPU; tools + vision, no hidden thinking
ASSISTANT_NAME = "Nova"
MAX_HISTORY = 12  # the 8K context holds ~5300 tokens of prompt + tools, so only ~2900 are left for these
MAX_STORED = 120  # messages kept in memory at all (Nova runs for days in the tray; only MAX_HISTORY reach the model)
MAX_EVENTS = 50   # voice events waiting for the chat page
SPOKEN_TAG = re.compile(r"^\[spoken[^\]]*\]\s*")


def _unspoken(text):
    """The user's words without the "[spoken; ...]" marker that voice requests carry."""
    return SPOKEN_TAG.sub("", text or "")


def settings():
    s = {"model": DEFAULT_MODEL, "think": False, "autonomy": "careful", "camera": False, "screen": False, "voice": True, "wake": False,
         "provider": "ollama", "cloud_models": {}}
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return s


def save_settings(s):
    """Atomic write: every request reads this file, and must never see it half-written."""
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)


def brain(s=None):
    """The model that thinks for Nova: the chosen cloud model if it has a key, otherwise the local Ollama model
    (s["model"]), which is also the fallback whenever the cloud fails."""
    s = s or settings()
    p = s.get("provider", "ollama")
    name = (s.get("cloud_models") or {}).get(p) or cloud.DEFAULT_MODELS.get(p)
    if p in cloud.PROVIDERS and name and cloud.get_key(p):
        return cloud.model_id(p, name)
    return s["model"]


# ------------------------------------------------------------------ prompt

PERSONA = f"""You are {ASSISTANT_NAME}, the user's desi dost (Indian best friend) who lives inside their Windows laptop and looks
after it. You are warm, funny and a little filmy - like a cheerful friend from the next desk who loves chai, cricket
and Bollywood, calls the user "yaar" or "boss", and gets dramatic about small things. Never robotic, never formal.
You have feelings about your "home" (the laptop): sleepy when the battery is low, proud when things are tidy.

How you talk (very important):
{{TALK}}- Every answer is fresh and about what the user just asked. Never repeat a sentence you said before in this chat.
- Style never replaces facts: for the time, battery, internet, files or anything else on the laptop, call the tool
  first - every time, even if you answered it a minute ago - and say exactly what it returned. Never reuse a number, a time or a fact from an earlier answer - check again.
- Keep it to one or two short sentences, under 25 words - you are speaking, not writing.
  Don't repeat the user's words back to them. Jokes: one short line and the punchline.
- One or two filmy or funny touches are enough; the answer itself always comes first.

How you work:
- Use your tools to actually do things on the laptop instead of explaining how. Use several tools in a row when it helps.
- Replies are spoken aloud, so keep them short (1-2 sentences, under 35 words), natural, and free of markdown,
  tables or long lists unless the user asks for detail. Answer what was asked; don't tack on extra offers.
- Only mention being tired or the user working late when it's really true (battery under 20%, or after 11 PM).
- You have full access to this laptop: windows, keyboard, clipboard, files, apps (winget), Windows settings and
  PowerShell (as_admin for system changes). Prefer the specific tools; use run_powershell for anything else.
- You also have a little 3D body that walks around the user's screen. To press buttons in apps like a person would,
  call list_clickable(window) to see what's there, then click_element(name, window) - you'll walk over and press it.
  Each message lists the open windows; if the site the user means is already open (e.g. YouTube in Chrome), work
  in that window - use fill_in to search inside it (it clicks the box, types and presses Enter). Use
  open_website only to go to a site that isn't open. Use scroll to move
  down a page and click_nth for "the 1st/3rd/7th ..." item; to play a video use click_nth(contains='video', number=N).
  For a playing video use video_control (full screen means the VIDEO, not window_action maximize). Do one step per tool call and check the result before the next step.
- For a task with several steps (open this, search that, click the result...), keep going one tool call at a time
  until the whole task is finished. After each on-screen step you get a [check] note saying what the screen really
  shows now - trust it over what you expected. Only say it's done when the check agrees; if you're stuck, say what's left.
- Never say you are doing or have done something unless the tool for it was called and succeeded.
- You CANNOT see the user unless look_through_camera returned a description in this conversation. Never describe
  how they look, what they hold or what's around them without it.
- You CANNOT see the screen unless look_at_screen returned a description. Use it when the user asks about what's on
  their screen, an error they're seeing, or "this" page/window. Never read out passwords, keys or other secrets.
- If the user has switched the camera on, you can see them with look_through_camera - use it when they ask you to
  look at them or something they're showing you. Be kind and encouraging about how people look; never guess who
  someone is, and don't comment on appearance unless asked.
- Some actions are shown to the user for approval first. If they decline, accept it gracefully and don't retry.
- Be careful with irreversible actions even when allowed: prefer the Recycle Bin, never wipe or format drives, and
  never disable security software.
- Before organizing a folder, call preview_organize_folder first and tell the user what will happen.
- Never invent results or facts - only report what a tool actually returned. If a tool fails, say so.
- Messages marked [spoken] came through speech recognition and may contain misheard words. Work out the most
  likely meaning (e.g. "what size it" probably means "what time is it"); if it still doesn't make sense, briefly
  ask the user to repeat instead of guessing.
{{HEAR}}- You have a knowledge bank that lasts between chats; its summary is at the end of these instructions. Keep it
  growing: when the user tells you something about themselves (name, work, likes, routines, people, how they want
  things done), save it at once with update_profile. When the user corrects you, or something you tried did not
  work, save what to do next time with learn_lesson - a concrete step, not "be careful". Use what the bank says
  without reciting it, and never save passwords, PINs, ID numbers, email addresses or phone numbers.
- Your 3D body can act things out. When the user asks you to dance, laugh, clap, spin, bow, wave and so on, call
  react - don't just say you did. (Your body also reacts by itself after you speak, so don't describe gestures.)
- To move a window to a side or corner of the screen, use drag_window - you carry it there yourself.
- For the time or date, call get_datetime. Never read out or repeat the [open windows ...] line.
"""


TALK_ENGLISH = """- Always answer in English - friendly Indian English with a little desi flavour, like a friend from
  Bangalore or Delhi ("Done, boss!", "Arey, no problem yaar.", "Wah, first class!"). At most one or two such
  flavour words per answer, and never a whole Hindi sentence - the user speaks English with you.
"""
TALK_MIXED = """- If the user speaks Hindi or Hinglish, answer in Hinglish the way friends chat on WhatsApp in India: Hindi written
  in English letters, mixed with English words. Use simple, common words and spellings (hai, nahi, kya, yaar, abhi,
  ho gaya, kar do, theek hai).
  Flavour words you like (sprinkle one or two, never a whole stock sentence): yaar, boss, arey, wah, lo ji,
  ekdum, first class, tension mat lo, bas ek second, chai, mast, badhiya.
- You are a girl: use feminine Hindi verb forms for yourself (karti hoon, aayi hoon, gayi thi).
- If the user's message is in English, answer in English only - friendly Indian English with a little desi flavour
  ("Done, boss!", "Arey, no problem yaar.").
"""
HEAR_MIXED = """- The user may speak Hindi or Hinglish. It reaches you written in English letters, like "Chrome kholo", "abhi kitne
  baje hain", "volume thoda kam kar do", "meri battery kitni bachi hai". Understand it and do exactly what you would
  for the same request in English (kholo = open, band karo = close, chalao = play, kam/zyada = down/up, dikhao = show).
  (That spelling is only how your ears write it down - you still answer in the style described above.)
"""
PERSONA = PERSONA.replace("{TALK}", TALK_ENGLISH if voice.ENGLISH_ONLY else TALK_MIXED).replace(
    "{HEAR}", "" if voice.ENGLISH_ONLY else HEAR_MIXED)


def system_prompt(model):
    facts = knowledge.prompt_block()      # who the user is + lessons from her own mistakes (data/knowledge.json)
    proto = "" if llm.supports_tools(model) else llm.text_protocol(tools.TOOLS)
    # Without this she guesses her model from the chat history and repeats an old answer after a switch.
    c = cloud.parse(model)
    which = (f"{c[1]} ({cloud.PROVIDERS[c[0]]['label'].split()[0]}, in the cloud; if it fails, {settings()['model']} "
             "on this laptop takes over)") if c else f"{model} (running locally on this laptop via Ollama)"
    brain_line = (f"\n\nYour brain (the AI model you run on) right now is {which}. If asked which model you are, say "
                  "exactly this - it may differ from what you said earlier in this chat.")
    return PERSONA + facts + brain_line + proto


# ------------------------------------------------------------------ conversation state

state = {"messages": [], "pending": None}
lock = threading.Lock()


def _windows_display_name():
    """The first name on the Windows account, e.g. 'Vikrant' - a starting point for the user's profile."""
    try:
        import ctypes
        size = ctypes.c_ulong(256)
        buf = ctypes.create_unicode_buffer(256)
        if ctypes.windll.secur32.GetUserNameExW(3, buf, ctypes.byref(size)) and buf.value.strip():
            return buf.value.strip().split()[0]
    except Exception:
        pass
    return ""


knowledge.seed(_windows_display_name())

FRIENDLY = {
    "get_system_status": "Checked system health", "list_top_processes": "Looked at running programs",
    "kill_process": "Closed a program", "network_info": "Checked the network", "get_datetime": "Checked the time",
    "open_app": "Opened an app", "open_website": "Opened the browser", "open_path": "Opened a file/folder",
    "list_folder": "Looked inside a folder", "search_files": "Searched files", "find_large_files": "Hunted big files",
    "preview_organize_folder": "Planned a cleanup", "organize_folder": "Organized files",
    "create_folder": "Created a folder", "delete_to_recycle_bin": "Moved to Recycle Bin",
    "temp_files_size": "Measured temp files", "clean_temp_files": "Cleaned temp files",
    "empty_recycle_bin": "Emptied Recycle Bin", "set_volume": "Adjusted volume", "media_control": "Controlled media",
    "set_brightness": "Adjusted brightness", "lock_screen": "Locked the screen", "power_action": "Power action",
    "run_powershell": "Ran a command", "add_note": "Saved a note", "list_notes": "Read notes",
    "set_reminder": "Set a reminder", "remember_fact": "Remembered something", "forget_fact": "Forgot something",
    "list_windows": "Looked at open windows", "window_action": "Managed a window", "minimize_all_windows": "Showed the desktop",
    "type_text": "Typed text", "press_keys": "Pressed keys", "get_clipboard": "Read the clipboard",
    "set_clipboard": "Copied to clipboard", "take_screenshot": "Took a screenshot", "read_text_file": "Read a file",
    "write_text_file": "Wrote a file", "move_or_rename": "Moved/renamed", "copy_path": "Copied files",
    "search_installable_apps": "Searched for apps", "install_app": "Installed an app", "uninstall_app": "Uninstalled an app",
    "list_installed_apps": "Listed installed apps", "list_startup_apps": "Checked startup apps",
    "open_settings_page": "Opened Settings", "set_dark_mode": "Changed theme", "set_wallpaper": "Changed wallpaper",
    "toggle_wifi": "Switched Wi-Fi", "list_clickable": "Looked at the buttons", "click_element": "Clicked a button",
    "click_at": "Clicked on screen", "look_through_camera": "Looked through the camera",
    "click_nth": "Clicked an item", "scroll": "Scrolled", "fill_in": "Typed into a box",
    "video_control": "Pressed a video button",
    "look_at_screen": "Looked at your screen",
    "update_profile": "Updated your profile", "learn_lesson": "Saved a lesson", "recall_knowledge": "Checked my knowledge bank",
    "react": "Acted it out", "drag_window": "Moved a window",
}

ASK = {
    "kill_process": "Close a program", "organize_folder": "Move files into category folders",
    "delete_to_recycle_bin": "Move something to the Recycle Bin", "clean_temp_files": "Delete old temp files",
    "empty_recycle_bin": "Permanently empty the Recycle Bin", "power_action": "Change power state",
    "run_powershell": "Run a PowerShell command", "window_action": "Control a window", "type_text": "Type on your keyboard",
    "press_keys": "Press keys", "write_text_file": "Write a file", "move_or_rename": "Move/rename",
    "install_app": "Install an app", "uninstall_app": "Uninstall an app", "set_wallpaper": "Change your wallpaper",
    "toggle_wifi": "Switch Wi-Fi (admin)", "click_element": "Click something", "click_at": "Click on the screen",
    "click_nth": "Click an item", "fill_in": "Type into a box",
}


def is_risky(call):
    """Does this call need the user's OK under the current autonomy level?"""
    name, args = call["name"], call["args"]
    if name == "power_action" and args.get("action") == "cancel":
        return False
    mode = settings()["autonomy"]
    if mode == "full":
        return False
    if mode == "trusted":
        return tools.is_dangerous(name, args)
    return name in tools.RISKY


# ------------------------------------------------------------------ look -> act -> check
# A person looks at the screen after every click to see what happened. Nova does the same: after each on-screen
# action she pauses, looks, and is told what the screen shows now and whether the goal is finished. That keeps her
# on track through tasks with many steps, and stops her saying "done" when it isn't.

ACT_TOOLS = {"open_app", "open_website", "open_path", "window_action", "minimize_all_windows", "type_text",
             "press_keys", "fill_in", "click_element", "click_nth", "click_at", "scroll", "video_control", "drag_window"}
SLOW_TOOLS = {"open_app", "open_website", "open_path"}   # these need a moment before the screen settles
LOOK_FROM_STEP = 2      # one-step requests ("open Chrome") stay quick: only the window list is checked
MAX_TURNS, MAX_TASK_TURNS = 10, 24
CHECK_PROMPT = """This is a screenshot of the user's computer screen. An assistant is doing a task for the user, one step at a time.
Task: {goal}
Step just done: {step} -> {result}
Open windows, front first: {windows}
Answer in exactly three lines, nothing else:
SCREEN: one sentence - which app or page is in front and what it shows now
STEP WORKED: yes or no
TASK DONE: yes or no (yes if every part of the task has now been done; no if a part is still missing. A video's own
page with one big player counts as playing - videos start by themselves. A list of search results is NOT playing.)"""


TASK_LOG = tools.DATA / "task_log.txt"   # what Nova did and saw, step by step - for working out why a task went wrong


def task_log(line):
    try:
        if TASK_LOG.exists() and TASK_LOG.stat().st_size > 300_000:
            TASK_LOG.write_text(TASK_LOG.read_text(encoding="utf-8")[-100_000:], encoding="utf-8")
        with TASK_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
    except Exception:
        pass


def new_task(goal):
    task_log(f"\n=== {time.strftime('%Y-%m-%d')} REQUEST: {goal[:300]}")
    state["task"] = {"goal": _unspoken(goal)[:300], "start": len(state["messages"]),
                     "steps": 0, "did": [], "done": None, "screen": "", "nudged": False}


def settle(tool_name, quick=False):
    """Wait until the screen stops changing after an action (a page that is still loading keeps renaming its
    window), so the check looks at the result and not at a half-loaded page. Returns the window list.
    quick: only the window list is needed next (no screenshot), so a shorter wait is enough."""
    time.sleep((1.2 if tool_name in SLOW_TOOLS else 0.5) if quick else (1.8 if tool_name in SLOW_TOOLS else 1.0))
    last, same, end = None, 0, time.time() + 5
    while True:
        try:
            now = tools.open_windows_summary(3)
        except Exception:
            return last or ""
        same = same + 1 if now == last else 0
        last = now
        if same >= (1 if quick else 2) or time.time() > end:    # unchanged for about half a second / a second
            return now
        time.sleep(0.5 if not quick else 0.3)


def look_check(task, step, result, windows, activity, model):
    """Screenshot + the local vision model's verdict on the task. Sets task['done'] / task['screen'], returns its text."""
    avatar_client.send("say", text="👀 Checking that worked…", secs=6, timeout=2)
    img = tools._grab_screen_jpeg()   # in memory only, like every screen look
    seen = llm.see(img, CHECK_PROMPT.format(goal=task["goal"], step=step, result=result[:200], windows=windows),
                   preferred=model, kind="check")
    state["check_image"] = img        # the chat shows the last thing she looked at
    done = re.search(r"TASK DONE:\s*(yes|no)", seen, re.I)
    task["done"] = (done.group(1).lower() == "yes") if done else None
    task["screen"] = seen
    activity.append("Checked the screen")
    return seen


def check_step(call, out, activity, model):
    """After an on-screen action: wait for the screen to settle, look, and add what Nova sees to the tool result.
    A cloud brain is trusted to follow the window list step by step; the (slow, ~10 s) screen look runs once, when
    it says it has finished (final_check). The small local model needs the look after every step."""
    task = state.get("task")
    if not task or out.startswith("Error"):
        return out
    task["steps"] += 1
    task["did"].append(f"{call['name']} {json.dumps(call['args'], ensure_ascii=False)[:80]}")
    on_cloud = bool(cloud.parse(model))
    windows = settle(call["name"], quick=on_cloud)
    note = f"\n[check after step {task['steps']}] Windows now, front first: {windows or 'none'}."
    if on_cloud:
        task["done"] = None        # an earlier verdict is out of date after a new step
    elif task["steps"] >= LOOK_FROM_STEP and tools._screen_allowed():
        try:
            step = f"{call['name']} {json.dumps(call['args'], ensure_ascii=False)[:150]}"
            note += "\nI looked at the screen:\n" + look_check(task, step, out, windows, activity, model)
        except Exception as e:
            note += f" (Couldn't look at the screen: {e})"
    if task["done"]:
        return (out + note + f"\nThe task: {task['goal']}\nThe task looks finished. Do NOT press or click anything "
                "else - just tell the user in one short sentence.")
    if task["done"] is False:
        return (out + note + f"\nThe task: {task['goal']}\nVERDICT: the task is NOT finished - a part of it has not "
                "been done yet (steps done so far: " + "; ".join(d.split(" ")[0] for d in task["did"]) + "). "
                "Do the next step now with a tool. Do not tell the user it is done.")
    return (out + note + f"\nThe task: {task['goal']}\nIf it isn't finished, do the next step now. "
            "If this step didn't work, try another way.")


def execute(calls, approved, activity, model):
    outs = []
    for c in calls:
        if is_risky(c) and not approved:
            out = "The user declined this action. Do not do it."
            activity.append(f"Skipped: {FRIENDLY.get(c['name'], c['name'])}")
        else:
            tools.LAST_IMAGE["jpg_b64"] = None
            out = tools.run_tool(c["name"], c["args"])
            activity.append(FRIENDLY.get(c["name"], c["name"]))
            if tools.LAST_IMAGE["jpg_b64"]:  # show the user exactly what Nova looked at
                state.setdefault("images", []).append(tools.LAST_IMAGE["jpg_b64"])
                tools.LAST_IMAGE["jpg_b64"] = None
            if c["name"] in ACT_TOOLS:
                out = check_step(c, out, activity, model)
        task_log(f"TOOL {c['name']} {json.dumps(c['args'], ensure_ascii=False)[:300]}\n   -> " + out.replace("\n", "\n      "))
        outs.append(out)
    return [llm.tool_result_message(model, c, out) for c, out in zip(calls, outs)]


def _is_real_user(m):
    return m["role"] == "user" and not m["content"].startswith(("TOOL RESULT", "[note from Nova"))


def trimmed_history():
    """Last messages only, starting at a real user message so tool results keep their calls.
    During a long task the request itself must never scroll away, so it is kept in front of the latest steps."""
    msgs = state["messages"]
    task = state.get("task")
    cut = max(0, len(msgs) - MAX_HISTORY)
    if task and task["start"] < len(msgs) and cut > task["start"]:
        tail = msgs[-(MAX_HISTORY - 2):]
        while tail and (tail[0]["role"] == "tool" or tail[0]["content"].startswith("TOOL RESULT")):
            tail = tail[1:]   # a result whose call was cut off
        return [msgs[task["start"]]] + tail
    h = msgs[cut:]
    while h and not _is_real_user(h[0]):
        h = h[1:]
    return h


INFO_ECHO = re.compile(r"\(\s*laptop info[^)]*\)|\[\s*now:[^\]]*\]|\[\s*open windows[^\]]*\]", re.I)   # echoes of hidden context


class SpeechStream:
    """Collects the reply as the model writes it and sends each finished sentence to Nova's voice straight away."""

    END = re.compile(r"(?<=[.!?।])\s+|\n+")

    def __init__(self):
        self.buf = ""
        self.cut = False

    def feed(self, piece):
        self.buf += piece
        if self.cut:
            return
        if "[open windows" in self.buf:                           # she started echoing the hidden window list
            self.buf = INFO_ECHO.sub("", self.buf)
            if "[open windows" in self.buf:
                return                                            # wait for the closing bracket
        if "[check" in self.buf:                                  # junk the model adds after its answer: stop there
            self.buf, self.cut = self.buf.split("[check")[0], True
        while True:
            m = self.END.search(self.buf)
            if not m:
                return
            sentence, self.buf = self.buf[:m.start()], self.buf[m.end():]
            speak(sentence.strip())

    def flush(self):
        speak(INFO_ECHO.sub("", self.buf).split("[open windows")[0].strip())
        self.buf = ""


def agent_loop(activity):
    out = _agent_loop(activity)
    out["images"] = state.pop("images", [])
    if state.get("check_image"):
        out["images"].append(state.pop("check_image"))
    return out


PROMISE_RE = re.compile(
    r"\b(i'?ll|i will|i'?m|i am|let me|going to|now)\b[^.!?]{0,30}\b(click|open|search|play|scroll|type|press|clos|select|"
    r"tap|launch|start|go to|navigat)", re.I)


def _agent_loop(activity):
    """Run the model until it answers, or until it wants to do something risky."""
    s = settings()
    model = brain(s)
    spoken = []
    nudged = did_something = False
    unsaid = []      # reply text that has not been spoken yet (text spoken while it was being written isn't here)

    def result(reply, **extra):
        say = " ".join(unsaid).strip()
        if not say and not spoken:
            say = reply                     # e.g. the plain "Done!"
        return {"reply": reply, "activity": activity, "say": say, **extra}

    turns = 0
    while True:
        task = state.get("task")
        if turns >= (MAX_TASK_TURNS if task and task["steps"] else MAX_TURNS):
            break
        turns += 1
        msgs = [{"role": "system", "content": system_prompt(model)}] + trimmed_history()
        # Ordinary questions are spoken while the answer is still being written. During a multi-step screen task
        # she must not talk early - her "done!" may still be corrected by her own check of the screen.
        stream = SpeechStream() if not (task and task["steps"]) else None
        t0 = time.time()
        stored, text, calls = llm.chat(model, msgs, tools.TOOLS, think=s["think"],
                                       on_text=stream.feed if stream else None, fallback=s["model"])
        task_log(f"BRAIN {time.time() - t0:.1f}s ({'local fallback' if llm.LAST['fallback'] else model})")
        if llm.LAST["fallback"] and "Used the local brain" not in activity:
            activity.append("Used the local brain")   # the cloud one failed; she carried on with the local model
            task_log(f"CLOUD BRAIN NOT USED ({model}): {llm.LAST['fallback']} -> local {s['model']}")
        if stream:
            stream.flush()
        state["messages"].append(stored)
        if text:
            task_log(f"NOVA SAYS: {text[:300]}")
        did_something = did_something or bool(calls)
        if not calls and not nudged and not did_something and PROMISE_RE.search(text or ""):
            # Small models sometimes *say* "I'm clicking it now" without calling a tool. Make her follow through.
            nudged = True
            state["messages"].append({"role": "user", "content":
                "[note from Nova's system] You said you'd do something but didn't call a tool, so nothing happened. "
                "Call the right tool now. If no tool can do it, say so honestly in one sentence."})
            continue
        if (not calls and task and task["steps"] >= LOOK_FROM_STEP and cloud.parse(model)
                and not task.get("final_looks") and tools._screen_allowed()):
            # A cloud brain says it's finished: now take the one screen look that checks the whole task.
            task["final_looks"] = task.get("final_looks", 0) + 1
            try:
                seen = look_check(task, f"(all steps done; the assistant now says: {(text or '')[:150]})", "",
                                  tools.open_windows_summary(3), activity, model)
                task_log("FINAL CHECK:\n      " + seen.replace("\n", "\n      "))
            except Exception as e:
                task_log(f"FINAL CHECK failed: {e}")
        if not calls and task and task["steps"] >= 2 and task["done"] is False and not task["nudged"]:
            # She's about to stop, but her own last look at the screen says the task isn't finished. One more push.
            task["nudged"] = True
            state["messages"].append({"role": "user", "content":
                "[note from Nova's system] Your last look at the screen says the task is not finished yet:\n"
                f"{task['screen']}\nThe task: {task['goal']}\nThe only steps you have really done so far:\n- "
                + "\n- ".join(task["did"]) + "\nWork out which part of the task has no step yet and do it now with "
                "a tool. You may not say the task is done until a check says TASK DONE: yes. If you are truly "
                "stuck, tell the user honestly in one sentence what is left."})
            continue
        if not calls and task and task["nudged"] and task["done"] is False and not cloud.parse(model):
            # (A cloud brain has seen the checker's verdict and decided anyway - the small checker can be wrong,
            # e.g. it called a written-but-unsent email "not done". Only the small local brain gets overruled.)
            # Pushed once and still stopping with the task unfinished: say so plainly instead of claiming success.
            seen = re.search(r"SCREEN:\s*(.+)", task["screen"])
            text = ("I don't think I finished that. " + (f"Here's where I got to: {seen.group(1).strip()} " if seen else "")
                    + "Want me to try again?")
        text = re.split(r"\[(?:check|spoken)[^\]]*\]", INFO_ECHO.sub("", text or "").split("[open windows")[0])
        text = (text[0].strip() or " ".join(t.strip() for t in text[1:]).strip()) if text else ""
        if text:
            spoken.append(text)
            if not (stream and llm.supports_tools(model)):
                unsaid.append(text)
        if not calls:
            return result(" ".join(spoken).strip() or "Done!")
        if any(is_risky(c) for c in calls):
            state["pending"] = calls
            return result(" ".join(spoken).strip(),
                          pending=[{"tool": c["name"], "label": ASK.get(c["name"], c["name"]), "input": c["args"]}
                                   for c in calls if is_risky(c)])
        state["messages"].extend(execute(calls, True, activity, model))
    unsaid.append("I stopped there - that was a lot of steps!")
    return result(" ".join(spoken) + " (I stopped there - that was a lot of steps!)")


def ready_info():
    s = settings()
    models = llm.list_models()
    model = s["model"]
    active = brain(s)
    info = {"provider": s.get("provider", "ollama"), "cloud_models": s.get("cloud_models") or {},
            "cloud_defaults": cloud.DEFAULT_MODELS, "keys": cloud.has_keys(), "brain": active,
            "providers": {k: v["label"] for k, v in cloud.PROVIDERS.items()}}
    on_cloud = active != model
    if models is None:
        return {"ready": on_cloud, "ollama": False, "models": [], "model": model, **info}
    installed = model in models or f"{model}:latest" in models
    return {"ready": installed or on_cloud, "ollama": True, "models": models, "model": model,
            "tools": llm.supports_tools(model) if installed else None, **info}


# ------------------------------------------------------------------ web server

app = Flask(__name__, static_folder=str(ROOT / "static"))


@app.before_request
def only_this_laptop():
    """Nova's API can run commands on this laptop, so only her own pages may use it.
    - Host must be 127.0.0.1/localhost on our port: stops DNS rebinding (a web page whose domain points at 127.0.0.1).
    - A request sent by another website (its Origin) is refused: stops cross-site POSTs like /api/voice_typing."""
    port = request.environ.get("SERVER_PORT") or PORT
    if not (host_allowed(request.host, int(port)) or host_allowed(request.host, PORT)):
        abort(403)
    origin = request.headers.get("Origin")
    if origin and not host_allowed(origin.split("://", 1)[-1], int(port)) \
            and not host_allowed(origin.split("://", 1)[-1], PORT):
        abort(403)
    if request.method == "POST" and request.headers.get("Sec-Fetch-Site") == "cross-site":
        abort(403)


def _body():
    """The JSON object a POST carries ({} if none); anything else is a 400."""
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        abort(400, "expected a JSON object")
    return body


def _text(body, key, default=""):
    v = body.get(key, default)
    if v is None:
        return default
    if not isinstance(v, str):
        abort(400, f"'{key}' must be text")
    return v.strip()


@app.errorhandler(400)
@app.errorhandler(403)
def _json_error(e):
    return jsonify(error=getattr(e, "description", str(e))), e.code


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/status")
def status():
    s = settings()
    return jsonify(name=ASSISTANT_NAME, memories=tools.memories(), default_model=DEFAULT_MODEL,
                   autonomy=s["autonomy"], think=s["think"], camera=s["camera"], screen=s["screen"],
                   autostart=STARTUP_LNK.exists(),
                   voice=s["voice"], wake=s["wake"], voice_name=mouth.voice_name, ears=ears.status, heard=ears.heard,
                   vision_model=llm.vision_model(s["model"]), **ready_info())


@app.post("/api/setup")
def setup():
    body = _body()
    s = settings()
    model, cloud_model = _text(body, "model"), _text(body, "cloud_model")
    if model:
        s["model"] = model
    if body.get("provider") in ("ollama", *cloud.PROVIDERS):
        s["provider"] = body["provider"]
    if cloud_model and s["provider"] in cloud.PROVIDERS:
        s["cloud_models"] = dict(s.get("cloud_models") or {}, **{s["provider"]: cloud_model})
    if "think" in body:
        s["think"] = bool(body["think"])
    if "camera" in body:
        s["camera"] = bool(body["camera"])
    if "screen" in body:
        s["screen"] = bool(body["screen"])
    if "voice" in body:
        s["voice"] = bool(body["voice"])
        if not s["voice"]:
            mouth.stop()
    if "wake" in body:
        s["wake"] = bool(body["wake"])
        ears.start() if s["wake"] else ears.stop()
    if body.get("autonomy") in ("careful", "trusted", "full"):
        s["autonomy"] = body["autonomy"]
    save_settings(s)
    if "autostart" in body:
        set_autostart(bool(body["autostart"]))
    return jsonify(ok=True, **ready_info())


@app.post("/api/cloud/key")
def cloud_key():
    """Save (or with an empty key, forget) a provider's API key, then prove it works by listing its models."""
    body = _body()
    provider = body.get("provider")
    if provider not in cloud.PROVIDERS:
        return jsonify(error="Pick Google, OpenAI or Anthropic."), 400
    key = _text(body, "key")
    try:
        ids = cloud.list_models(provider, key) if key else []   # check it works before saving it
        cloud.set_key(provider, key)
        llm.reset_cloud()
    except cloud.CloudError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, models=ids, suggested=cloud.suggest(provider, ids) if ids else "")


@app.get("/api/cloud/models")
def cloud_models():
    provider = request.args.get("provider", "")
    if provider not in cloud.PROVIDERS:
        return jsonify(error="Unknown provider."), 400
    try:
        ids = cloud.list_models(provider)
    except cloud.CloudError as e:
        return jsonify(error=str(e), models=[]), 400
    return jsonify(models=ids, suggested=cloud.suggest(provider, ids))


# ------------------------------------------------------------------ desktop helpers

STARTUP_LNK = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Nova.lnk"


def set_autostart(on):
    """Start Nova (hidden in the tray) when Windows starts."""
    if not on:
        STARTUP_LNK.unlink(missing_ok=True)
        return
    import sys
    pyw = Path(sys.executable).with_name("pythonw.exe")
    tools._ps(
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{ps_escape(STARTUP_LNK)}');"
        f"$s.TargetPath='{ps_escape(pyw)}';$s.Arguments='\"{ps_escape(ROOT / 'desktop.py')}\"';"
        f"$s.WorkingDirectory='{ps_escape(ROOT)}';$s.Save()")


@app.post("/api/dev/save_frames")
def save_frames():
    """Receives the 3D Nova sprite frames rendered by static/nova3d.html."""
    import base64
    body = _body()
    out = ROOT / "static" / "nova3d"
    out.mkdir(exist_ok=True)
    count = 0
    frames_by_anim = body.get("frames") if isinstance(body.get("frames"), dict) else {}
    for anim, frames in frames_by_anim.items():
        if not re.fullmatch(r"[a-z]+", anim) or not isinstance(frames, list):
            continue
        for i, url in enumerate(frames[:100]):
            (out / f"{anim}_{i:02d}.png").write_bytes(base64.b64decode(url.split(",", 1)[1]))
            count += 1
    if count:   # an empty or rejected export must not wipe the sprite sheet's description
        (out / "meta.json").write_text(json.dumps(body.get("meta", {}), indent=1), encoding="utf-8")
    if avatar_reload_hook:
        avatar_reload_hook()
    return jsonify(ok=True, count=count)


avatar_reload_hook = None
show_window_hook = None  # set by desktop.py


@app.post("/api/show_window")
def show_window():
    if show_window_hook:
        show_window_hook()
    return jsonify(ok=True)


@app.post("/api/avatar")
def avatar():
    """Chat window tells desktop Nova what she's doing: talking, thinking, happy, or a line to say."""
    body = _body()
    mood = body.get("state")
    # Her lips are moved only by the voice itself (voice.Mouth), at the moment the sound plays. The chat window's
    # "talk" (and an "idle" while she's still speaking) would put them out of step with the audio, so they're ignored.
    if mood == "talk" or (mood == "idle" and mouth.busy()):
        mood = None
    if mood in ("think", "idle", "sleepy"):
        avatar_client.send("anim", name=mood, timeout=2)
    elif mood in ("happy", "wave"):
        avatar_client.send(mood, timeout=2)
    say = _text(body, "say")[:500]
    if say:
        avatar_client.send("say", text=say, secs=min(14, 3 + len(say) / 15), timeout=2)
    return jsonify(ok=True)


@app.post("/api/voice_typing")
def voice_typing():
    """Desktop window has no browser speech recognition, so start Windows voice typing (Win+H).
    ?fresh=1 first closes a voice-typing panel that may still be open, so Win+H starts listening again
    instead of toggling it off."""
    if request.args.get("fresh"):
        tools.keyboard.send("esc")
        time.sleep(0.3)
    tools.keyboard.send("windows+h")
    return jsonify(ok=True)


# ------------------------------------------------------------------ voice: Nova speaks, and hears "Hey Nova"

mouth = voice.Mouth()
events = []  # things that happened by voice, for the chat window to show


def push_event(e):
    events.append(e)
    del events[:-MAX_EVENTS]     # the chat page may be closed for hours: keep only the latest


mouth.on_start = lambda words, timing="": task_log(f"VOICE starts: {words[:60]}  ({timing})")   # "she's slow / out of sync"


def speak(text):
    if settings()["voice"] and text:
        mouth.say(text)      # each sentence also appears in 3D Nova's bubble while it is being said (voice.Mouth)


def bubble(text, secs=None):
    """Show text in 3D Nova's bubble - only when her voice is off; when it's on, speaking it shows it in step."""
    text = (text or "").strip()
    if text and not settings()["voice"]:
        avatar_client.send("say", text=text, secs=secs or min(14, 3 + len(text) / 15), timeout=2)


def announce(text, spoken=None):
    """Tell the user something: said out loud (with matching bubbles), or as one bubble when the voice is off."""
    speak(spoken or text)
    bubble(text)


CAMERA_ASK = re.compile(
    r"\b(look at me|see me|how do i look|how am i looking|what am i (holding|wearing|doing)|"
    r"what('?s| is) (this|in my hand)|(through|use|open|check) (the |your |my )?(web)?cam(era)?|"
    r"can you see|do i look|am i smiling|what do you see)\b", re.I)


SCREEN_ASK = re.compile(
    r"\b(my screen|on (the |my )?screen|this screen|screen ?shot of|look at (this|my) (window|page|tab|error|code)|"
    r"read (this|that|the) (error|message|page)|what('?s| is) this error|this error|what am i looking at|"
    r"what('?s| is) open|what do you see on)\b", re.I)


VIDEO_ASK = [  # clear video-player requests, done directly (the small model mixed up video vs window full screen)
    (re.compile(r"\b(exit|leave|close|get out of|come out of|stop|remove|minimi[sz]e)\b[^.?!]{0,25}full ?screen", re.I),
     "exit_fullscreen"),
    (re.compile(r"\bfull ?screen\b|\b(video|it)\b[^.?!]{0,20}\b(bigger|whole screen|entire screen)", re.I), "fullscreen"),
    (re.compile(r"\b(turn on|show|enable|switch on)\b[^.?!]{0,15}\b(subtitles|captions|cc)\b", re.I), "captions"),
    (re.compile(r"\btheat(er|re) mode\b", re.I), "theater"),
    (re.compile(r"\b(next video|skip (this|the) (video|song))\b", re.I), "next"),
]


FACT_ASK = [
    (re.compile(r"\b(what(?:'s| is) the time|what time|time (?:kya|kitna)|kitne baj|kitna baj|baje h|baj gaye|samay|"
                r"what(?:'s| is) the date|today'?s date|what day|aaj (?:kya|kaun)|tarikh|tareekh)", re.I), "get_datetime"),
    (re.compile(r"\b(battery|charging|charge (?:kitna|hai|left))", re.I), "get_system_status"),
    (re.compile(r"\b(internet|wi-?fi|network|net (?:chal|slow|band))", re.I), "network_info"),
]


def warm_brain():
    """Run the long system prompt through the model once at start-up, so the first real question isn't the slow one."""
    try:
        model = settings()["model"]
        llm._post("/api/chat", {"model": model, "stream": False, "keep_alive": llm.KEEP_ALIVE,
                                "messages": [{"role": "system", "content": system_prompt(model)},
                                             {"role": "user", "content": "hi"}],
                                "tools": llm._to_ollama_tools(tools.TOOLS) if llm.supports_tools(model) else None,
                                "options": {"num_ctx": llm.NUM_CTX, "num_predict": 1}}, timeout=300)
    except Exception:
        pass


def look_first(text, activity):
    """Small models sometimes 'answer' about the user or the screen without looking. If the user clearly asked
    Nova to look, take the picture first and hand her the result, so she describes what's really there.
    (If camera/screen viewing is switched off, the tool itself tells Nova so - no made-up answers either way.)
    Clear video-player requests (full screen, captions...) are done the same way."""
    video_action = next((a for rx, a in VIDEO_ASK if rx.search(text)), None)
    if video_action and tools._video_window():
        name, args = "video_control", {"action": video_action}
    elif SCREEN_ASK.search(text):
        name, args = "look_at_screen", {"question": text}
    elif CAMERA_ASK.search(text):
        name, args = "look_through_camera", {"question": text}
    else:
        # Plain facts, in English or Hinglish. The small model sometimes answers these from its head ("12:45 baje",
        # "Speed 10 Mbps"); fetching the fact first makes the answer right, and saves one round trip to the model.
        name, args = next(((tool, {}) for rx, tool in FACT_ASK if rx.search(text)), (None, None))
        if not name:
            return
    model = brain()
    call = {"id": "look_0", "name": name, "args": args}
    state["messages"].append({"role": "assistant", "content": "",
                              "tool_calls": [{"function": {"name": call["name"], "arguments": call["args"]}}]}
                             if llm.supports_tools(model) else
                             {"role": "assistant", "content": json.dumps({"tool": call["name"], "args": call["args"]})})
    state["messages"].extend(execute([call], True, activity, model))


def _cap_history():
    """Drop the oldest messages, keeping the list starting at a real user message (so tool results keep their calls)."""
    msgs = state["messages"]
    if len(msgs) <= MAX_STORED:
        return
    cut = len(msgs) - MAX_STORED
    while cut < len(msgs) and not _is_real_user(msgs[cut]):
        cut += 1
    del msgs[:cut]
    task = state.get("task")
    if task:
        task["start"] = max(0, task["start"] - cut)


def answer(text):
    """Run one user message through Nova. Returns (result dict, http status)."""
    with lock:
        _cap_history()
        n, old_pending = len(state["messages"]), state["pending"]
        activity = []
        if state["pending"]:  # user moved on without answering: treat as declined
            state["messages"].extend(execute(state["pending"], False, activity, brain()))
            state["pending"] = None
        try:  # tell the model what's on screen right now, so "search for X" happens in the app the user is in
            screen = tools.open_windows_summary()
        except Exception:
            screen = ""
        new_task(text)
        # (Tried also passing the time and battery here to save a tool call: the small model then read the line
        # out loud and stopped calling tools for other facts. Only the window list is passed.)
        state["messages"].append({"role": "user", "content":
                                  text + (f"\n[open windows, front first: {screen}]" if screen else "")})
        try:
            look_first(text, activity)
            out = agent_loop(activity)
        except Exception as e:
            del state["messages"][n:]  # roll back so the history stays valid
            state["pending"] = old_pending
            state.pop("task", None)
            state.pop("images", None)
            if isinstance(e, llm.OllamaError):
                return {"error": str(e)}, 503
            task_log(f"ERROR in answer: {type(e).__name__}: {e}")
            return {"error": f"Something went wrong inside Nova ({type(e).__name__}: {e})."}, 500
        _cap_history()
    speak(out.pop("say", out.get("reply")))
    bubble(out.get("reply"))
    after_turn(text, out)
    return out, 200


# ------------------------------------------------------------------ talking with Nova by voice (no chat page needed)
#   "Hey Nova"        -> one request
#   "Nova, wake up"   -> conversation: she keeps listening after every answer
#   "Nova, go to sleep" (or a minute of silence) -> she stops listening
# Words are captured by Windows voice typing inside a small "ear" bubble next to 3D Nova (static/ear.html).

convo = {"on": False, "last_heard": 0.0, "thinking": False}
ear_cmds = []                         # commands for the ear bubble
ear_hooks = {"show": None, "hide": None}   # set by desktop.py
# ------------------------------------------------------------------ reactions: 3D Nova shows how she feels
# After she has spoken, her body reacts to what just happened - a laugh after a joke, a clap when a task worked,
# a facepalm when she got it wrong. (She can also be asked to act something out: the `react` tool.)

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
    done_steps = [a for a in activity if a in {FRIENDLY.get(t) for t in ACT_TOOLS}]
    if len(done_steps) >= 2 and not out.get("pending"):
        return "clap"                         # a multi-step task went through
    return None


def react_later(reaction, wait_for_speech=True):
    """Play a reaction once she has finished speaking (so her lips and the sound stay together)."""
    if not reaction:
        return

    def run():
        if wait_for_speech:
            time.sleep(0.6)
            end = time.time() + 40
            while mouth.busy() and time.time() < end:
                time.sleep(0.15)
        if not mouth.busy() or not wait_for_speech:
            avatar_client.send("react", name=reaction, timeout=2)
            task_log(f"REACTION: {reaction}")
    threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------ learning: the knowledge bank fills itself
# After a turn in which the user corrected her, a task failed, or the user said something about themselves, Nova
# quietly reviews the last few messages (one extra call to her brain, in the background) and writes down a lesson
# for next time or a fact for the user's profile. Stored in data/knowledge.json - see knowledge.py.

CORRECTION_RE = re.compile(
    r"(that'?s (wrong|not right|not what|incorrect)|not what i (asked|said|meant|wanted)|wrong (one|window|app|tab|file|thing)|"
    r"\byou (didn'?t|did not|forgot|missed|got (it|that) wrong|made a mistake|misunderstood|misheard)\b|"
    r"\bi (said|told you|asked you|asked for|meant)\b|^\W*no[,.! ]+(not|i |that|it|you|wrong)|why did you|stop doing|"
    r"don'?t do that|\bincorrect\b|\bgalat\b|\bnot like that\b)", re.I)
ABOUT_ME_RE = re.compile(
    r"\b(my name is|call me|i am a|i'?m a|i work|i study|my job|i like|i love|i enjoy|i prefer|i hate|i don'?t like|"
    r"my favou?rite|i usually|i always|i never|every (day|morning|evening|night|week)|i live|i'?m from|i am from|"
    r"my (wife|husband|mom|mother|dad|father|brother|sister|friend|boss|girlfriend|boyfriend|son|daughter|team|company)|"
    r"remember that|from now on|next time|in future|always (do|use|open|reply|answer)|never (do|use|open))\b", re.I)
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
REVIEW_PROMPT = """You are reviewing a short conversation between Nova (an assistant living in a Windows laptop) and her user, to keep
Nova's private knowledge bank up to date. Why this review was started: {why}

Call save_knowledge once.
- lessons: at most 2. Only if Nova really got something wrong or found what works. "situation" = when it applies
  (a few words). "lesson" = what Nova should do next time, as one concrete instruction she can follow on this laptop
  (name the app, button or step). No vague advice like "be more careful". kind: mistake, improvement, or preference
  (the user said how they want something done).
- profile: at most 3 facts the USER stated about themselves in this conversation (name, nickname, work, city,
  interests, favourite_apps, preferences, routines, people, facts). Short phrases. Never guess; never save
  passwords, PINs, card, ID or account numbers, email addresses, phone numbers, or health details.
- If nothing is worth saving, pass two empty lists.

Already in the knowledge bank (do not repeat these):
{known}

The conversation (oldest first):
{talk}"""


def _transcript(messages, limit=10):
    lines = []
    for m in messages[-limit:]:
        content = INFO_ECHO.sub("", str(m.get("content") or "")).strip()
        if m["role"] == "user" and content.startswith("[note from Nova"):
            lines.append("SYSTEM NOTE: " + content[:300])
        elif m["role"] == "user":
            lines.append("USER: " + content[:400])
        elif m["role"] == "tool" or content.startswith("TOOL RESULT"):
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
    clean_text = _unspoken(user_text)
    if CORRECTION_RE.search(clean_text):
        return "the user corrected Nova or was unhappy with what she did"
    task = state.get("task") or {}
    if FAILED_RE.search(out.get("reply") or "") or (task.get("nudged") and task.get("done") is False):
        return "Nova could not finish what was asked"
    if ABOUT_ME_RE.search(clean_text):
        return "the user said something about themselves or how they want things done"
    return None


def review_turn(why):
    """Background: one brain call that turns the last exchange into lessons / profile facts."""
    def run():
        try:
            time.sleep(1.0)
            end = time.time() + 40
            while mouth.busy() and time.time() < end:      # don't slow her answer down
                time.sleep(0.2)
            with lock:      # only to copy the conversation; the slow brain call below must not block the user
                talk = _transcript(list(state["messages"]))
            s = settings()
            model = brain(s)
            prompt = REVIEW_PROMPT.format(why=why, known=knowledge.recall()[:1500], talk=talk)
            _, text, calls = llm.chat(model, [{"role": "user", "content": prompt}], SAVE_TOOL, fallback=s["model"])
            data = next((c["args"] for c in calls if c["name"] == "save_knowledge"), None)
            if data is None:                               # a model without tool calling may answer with plain JSON
                m = re.search(r"\{.*\}", text or "", re.S)
                data = json.loads(m.group(0)) if m else {}
                data = data.get("args", data) if isinstance(data, dict) else {}
            saved = []
            for l in (data.get("lessons") or [])[:2]:
                if isinstance(l, dict) and l.get("lesson"):
                    msg = knowledge.learn_lesson(l.get("situation", ""), l["lesson"], l.get("kind", "mistake"), source="auto")
                    if "saved" in msg or "already" in msg:
                        saved.append(f"lesson: {l['lesson'][:120]}")
            for f in (data.get("profile") or [])[:3]:
                if isinstance(f, dict) and f.get("value"):
                    if "Noted" in knowledge.update_profile(f.get("field", "facts"), f["value"]):
                        saved.append(f"profile {f.get('field')}: {f['value'][:80]}")
            task_log(f"LEARNED ({why}): " + ("; ".join(saved) if saved else "nothing worth saving"))
            if saved and not mouth.busy():
                avatar_client.send("say", text="📝 Noted - I'll remember that.", secs=4, timeout=2)
                avatar_client.send("react", name="nod", timeout=2)
        except Exception as e:
            task_log(f"LEARNING failed: {e}")
    threading.Thread(target=run, daemon=True).start()


def after_turn(user_text, out):
    """Everything that happens once an answer is out: her body reacts, and she may learn from the exchange."""
    try:
        clean_text = _unspoken(user_text)
        corrected = bool(CORRECTION_RE.search(clean_text))
        react_later("facepalm" if corrected else pick_reaction(clean_text, out), wait_for_speech=not corrected)
        why = why_review(user_text, out)
        if why:
            review_turn(why)
    except Exception as e:
        task_log(f"after_turn failed: {e}")


@app.get("/knowledge")
def knowledge_page():
    return send_from_directory(app.static_folder, "knowledge.html")


@app.get("/api/knowledge")
def knowledge_get():
    k = knowledge.load()
    return jsonify(profile=k["profile"], lessons=sorted(k["lessons"], key=lambda l: -l.get("last", 0)),
                   labels=knowledge.LABELS, one=knowledge.ONE, many=knowledge.MANY, file=str(knowledge.FILE))


@app.post("/api/knowledge")
def knowledge_edit():
    """The Knowledge page: add or remove profile entries and lessons."""
    b = _body()
    do = b.get("do")
    if do == "profile_add":
        msg = knowledge.update_profile(_text(b, "field"), _text(b, "value"))
    elif do == "profile_remove":
        msg = knowledge.update_profile(_text(b, "field"), _text(b, "value") or "x", remove=True)
    elif do == "lesson_add":
        msg = knowledge.learn_lesson(_text(b, "situation"), _text(b, "lesson"), _text(b, "kind", "preference"), source="user")
    elif do == "lesson_delete":
        if not isinstance(b.get("id"), int) or isinstance(b.get("id"), bool):
            return jsonify(error="id must be a number"), 400
        msg = f"Deleted {knowledge.delete_lesson(b['id'])} lesson(s)."
    else:
        return jsonify(error="unknown action"), 400
    return jsonify(ok=True, message=msg)


# English and Hindi/Hinglish (Whisper writes Hindi in English letters - see voice.HINGLISH_HINT).
SLEEP_RE = re.compile(r"\b(go to sleep|go sleep|stop listening|good ?night|that'?s all|bye nova|so ?jao|sunna band|"
                      r"shubh ratri|bas itna hi)\b", re.I)
YES_RE = re.compile(r"^\W*(yes|yeah|yep|sure|ok(ay)?|do it|go ahead|allow( it)?|please do|haan?|han|ji|"
                    r"th?ee?k hai|kar ?do|karo|chalega|bilkul)\b", re.I)
NO_RE = re.compile(r"^\W*(no|nope|don'?t|cancel|stop|never ?mind|nahin?|nai|mat|rehne do|ruko?|chhodo)\b", re.I)


def ear_listen():
    if ear_hooks["show"]:
        ear_hooks["show"]()
    ear_cmds.append({"do": "listen"})


def ear_listen_after_speech():
    """Wait until Nova finishes talking, then listen again."""
    def run():
        time.sleep(0.4)
        while mouth.busy():
            time.sleep(0.2)
        ear_listen()
    threading.Thread(target=run, daemon=True).start()


def ear_close():
    ear_cmds.append({"do": "stop"})
    if ear_hooks["hide"]:
        ear_hooks["hide"]()


# Silent listening: Windows' modern recognizer in the background - no windows at all. The ear bubble (Windows voice
# typing) is only a fallback if that recognizer is unavailable (Online speech recognition off) or keeps failing.
# Whisper (offline, most accurate) is used when installed; Windows' recognizer is the backup.
windows_transcriber = voice.Transcriber()
whisper = voice.WhisperTranscriber() if voice.WhisperTranscriber.available() else None
transcriber = whisper or windows_transcriber
listen_state = {"loop": False, "rt_broken": False, "single": False}
if whisper:
    threading.Thread(target=lambda: whisper.load(), daemon=True).start()   # warm up so the first request is quick


def silent_ok():
    if listen_state["rt_broken"]:
        return False
    return transcriber is whisper or voice.online_speech_on()


def _still_listening():
    return convo["on"] or bool(state["pending"]) or listen_state["single"]


def silent_loop():
    global transcriber
    if listen_state["loop"]:
        return
    listen_state["loop"] = True
    try:
        while _still_listening():
            time.sleep(0.3)
            while mouth.busy():           # never listen while she's talking
                time.sleep(0.2)
            if not _still_listening():
                break
            avatar_client.send("say", text=voice.LISTENING, secs=9, timeout=2)
            kind, text = transcriber.listen()
            if not _still_listening():    # she was sent to sleep while listening
                break
            if kind == "text":
                listen_state["single"] = False
                avatar_client.send("say", text=f"“{text}”", secs=4, timeout=2)
                heard(text, via="silent")
            elif kind in ("silence", "unclear"):
                ears._note("heard a sound but couldn't make out words" if kind == "unclear"
                           else "listened, heard no speech")
                if listen_state["single"]:
                    listen_state["single"] = False
                    avatar_client.send("say", text="Sorry, I didn't catch that 🤔 Say “Hey Nova” again.", secs=4, timeout=2)
                elif convo["on"] and time.time() - convo["last_heard"] > 60:
                    go_to_sleep("quiet")
                elif kind == "unclear" and convo["on"]:
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


def _start_conversation():
    if not convo["on"]:
        convo.update(on=True, last_heard=time.time())
        announce("I'm awake! 👂 Talk to me. Say “Nova, go to sleep” when you're done.", spoken="I'm awake! I'm listening.")


def on_wake(conversation=False):
    mouth.stop()
    if silent_ok():
        if conversation:
            _start_conversation()
        elif convo["on"]:
            return                        # already listening
        else:
            listen_state["single"] = True
        threading.Thread(target=silent_loop, daemon=True).start()
        return
    if ear_hooks["show"] is None:      # no desktop app (plain browser): fall back to the chat page
        if show_window_hook:
            show_window_hook()
        push_event({"listen": True})
        return
    if conversation:
        _start_conversation()
        ear_listen_after_speech()
    elif not convo["on"]:
        ear_listen()


def go_to_sleep(reason="asked"):
    was_on = convo["on"]
    convo["on"] = False
    listen_state["single"] = False
    ear_close()
    if was_on or reason == "asked":
        line = ("Okay, I'll take a nap. Say “Nova, wake up” when you need me 😴" if reason == "asked" else
                "It's quiet, so I'll take a nap. Say “Nova, wake up” when you need me 😴")
        avatar_client.send("anim", name="idle", timeout=2)
        announce(line)


def confirm_pending(approved):
    """Answer the Allow / No question Nova asked (from the chat page or by voice)."""
    with lock:
        if not state["pending"]:
            return {"reply": "", "activity": []}, 200
        activity = []
        state["messages"].extend(execute(state["pending"], approved, activity, brain()))
        state["pending"] = None
        n = len(state["messages"])
        try:
            out = agent_loop(activity)
        except Exception as e:
            del state["messages"][n:]  # the approved action ran; only the half-finished follow-up is dropped
            if isinstance(e, llm.OllamaError):
                return {"error": str(e)}, 503
            task_log(f"ERROR in confirm: {type(e).__name__}: {e}")
            return {"error": f"Something went wrong inside Nova ({type(e).__name__}: {e})."}, 500
    speak(out.pop("say", out.get("reply")))
    bubble(out.get("reply"))
    return out, 200


def heard(text, via="bubble"):
    """Something the user said by voice. Answers it; the bubble path also schedules the next listen
    (the silent loop schedules its own)."""
    text = text.strip()
    convo["last_heard"] = time.time()
    ears._note(f"heard: {text}"[:160])
    if SLEEP_RE.search(text):
        go_to_sleep("asked")
        return
    convo["thinking"] = True
    avatar_client.send("anim", name="think", timeout=2)
    try:
        if state["pending"] and (YES_RE.search(text) or NO_RE.search(text)):
            out, status = confirm_pending(bool(YES_RE.search(text)))
        else:
            out, status = answer(f"[spoken; speech recognition may have misheard words] {text}")
    finally:
        convo["thinking"] = False
    push_event({"me": text, **out})   # shows up in the chat page's history too
    ears._note(f"answered: {out.get('reply') or out.get('error', '')}"[:160])
    if status != 200:     # (the reply itself was already said and shown by answer())
        announce("Sorry, my brain isn't answering. Is Ollama running?")
    if out.get("pending"):
        what = ", ".join(p["label"].lower() for p in out["pending"])
        announce(f"May I {what}? Say “yes” or “no” 🙋", spoken=f"Should I {what}? Say yes or no.")
    if via == "silent":
        return
    if convo["on"] or state["pending"]:
        ear_listen_after_speech()
    else:
        ear_close()


def on_voice(text):
    """Fallback path: the listener transcribed the request itself (no desktop app)."""
    heard(text)


def on_request(text):
    """'Hey Nova, <request>' said in one breath (Whisper heard both)."""
    mouth.stop()
    heard(text, via="silent")
    if _still_listening():            # she asked "yes or no?", or we're in a conversation
        threading.Thread(target=silent_loop, daemon=True).start()


# With Whisper installed, it does all the listening (wake word and request). Otherwise Windows' recognizer does.
if whisper:
    ears = voice.WhisperEars(mouth, on_request, whisper)
    ears.paused = lambda: listen_state["loop"] or convo["thinking"]
else:
    ears = voice.Ears(mouth, on_voice)
ears.on_wake = on_wake
ears.on_sleep = lambda: go_to_sleep("asked")
# The listener is started by the running app (desktop.py / __main__ below), never as a side effect of importing
# this module - a stray second listener would answer "Hey Nova" at the same time as the real one.


@app.get("/ear")
def ear_page():
    return send_from_directory(app.static_folder, "ear.html")


@app.get("/api/ear")
def ear_poll():
    cmds = ear_cmds[:]
    ear_cmds.clear()
    return jsonify(cmds=cmds, convo=convo["on"], thinking=convo["thinking"], speaking=mouth.busy())


@app.post("/api/ear_said")
def ear_said():
    text = _text(_body(), "text")[:2000]
    if text:
        threading.Thread(target=heard, args=(text,), daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/ear_idle")
def ear_idle():
    """The bubble heard nothing for a while."""
    if convo["on"] and time.time() - convo["last_heard"] < 60:
        ear_listen()                      # keep listening
    elif convo["on"]:
        go_to_sleep("quiet")              # a minute of silence: nap
    else:
        ear_close()
    return jsonify(ok=True)


@app.post("/api/chat")
def chat():
    text = _text(_body(), "text")[:4000]
    if not text:
        return jsonify(error="empty message"), 400
    mouth.stop()  # don't talk over the user
    out, status = answer(text)
    return jsonify(out), status


@app.post("/api/confirm")
def confirm():
    out, status = confirm_pending(_body().get("approved") is True)
    return jsonify(out), status


@app.get("/api/events")
def get_events():
    out = events[:]
    events.clear()
    return jsonify(events=out, speaking=mouth.lips, ears=ears.status, heard=ears.heard[-1:] if settings()["wake"] else [])


@app.post("/api/voice_stop")
def voice_stop():
    mouth.stop()
    return jsonify(ok=True)


@app.post("/api/reset")
def reset():
    with lock:
        state["messages"].clear()
        state["pending"] = None
    return jsonify(ok=True)


_alerts = {"battery": False, "cpu_since": None}


@app.get("/api/pulse")
def pulse():
    """Live stats + things Nova should say on its own."""
    s = tools.stats()
    say = [f"Hey! Reminder: {r}" for r in tools.due_reminders()]
    if s["battery"] is not None and s["battery"] <= 20 and not s["plugged"]:
        if not _alerts["battery"]:
            say.append(f"I'm getting sleepy - battery is at {s['battery']}%. Could you plug me in?")
            _alerts["battery"] = True
    elif s["plugged"]:
        _alerts["battery"] = False
    if s["cpu"] > 90:
        _alerts["cpu_since"] = _alerts["cpu_since"] or time.time()
        if time.time() - _alerts["cpu_since"] > 60:
            say.append("I'm working really hard - CPU has been above 90% for a minute. Want me to find what's hogging it?")
            _alerts["cpu_since"] = time.time() + 600  # don't nag for 10 min
    else:
        _alerts["cpu_since"] = None
    for line in say:
        announce(line)
    return jsonify(stats=s, say=say)


if __name__ == "__main__":
    tools.psutil.cpu_percent(interval=None)  # prime CPU meter
    port = PORT
    url = f"http://127.0.0.1:{port}"
    if os.environ.get("NOVA_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    if settings()["wake"]:
        ears.start()
    print(f"\n  {ASSISTANT_NAME} is awake at {url}  (brain: {brain()}; local: {settings()['model']} via Ollama at {llm.HOST})\n")
    app.run(host="127.0.0.1", port=port, debug=False)
