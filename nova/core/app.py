"""Nova - a friendly assistant that lives in your laptop, powered by a local Ollama model.

Run:  python app.py   (opens http://127.0.0.1:5055)
"""
import json
import os
import random
import re
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

import ctypes

from nova.avatar import avatar_client
from nova.avatar import body as body_module
from nova.brain import cloud
from nova.core import characters
from nova.actions import coworker
from nova.actions import knowledge
from nova.brain import llm
from nova.actions import mcp
from nova.actions import gmail
from nova.actions import notifications
from nova.actions import phone as phone_link
from nova.core import mobile as mobile_mod
from nova.actions import presence
from nova.actions import watcher
from nova.brain import retrieval
from nova.audio import sfx
from nova.actions import skills
from nova.actions import checks
from nova.actions import recorder
from nova.brain import toolpick
from nova.brain import router
from nova.actions import tools
from nova.audio import voice
from nova import shared
from nova.shared import REACTIONS, host_allowed, ps_escape

ROOT = shared.ROOT
PORT = int(os.environ.get("PORT", 5055))
SETTINGS_FILE = tools.DATA / "settings.json"
DEFAULT_MODEL = "qwen3-vl:4b-instruct"  # fits a 6 GB laptop GPU; tools + vision, no hidden thinking
ASSISTANT_NAME = "Nova"
MAX_HISTORY = 12  # the 8K context holds ~5300 tokens of prompt + tools, so only ~2900 are left for these
MAX_STORED = 120  # messages kept in memory at all (Nova runs for days in the tray; only MAX_HISTORY reach the model)
MAX_EVENTS = 50   # voice events waiting for the chat page
SPOKEN_TAG = re.compile(r"^\[(spoken|from the user's phone)[^\]]*\]\s*")   # voice and 📱 phone requests carry one


def _unspoken(text):
    """The user's words without the "[spoken; ...]" marker that voice requests carry."""
    return SPOKEN_TAG.sub("", text or "")


def settings():
    s = {"model": DEFAULT_MODEL, "think": False, "autonomy": "careful", "camera": False, "screen": False, "voice": True, "wake": False,
         "provider": "ollama", "cloud_models": {}, "notifications": False, "notif_sleep": False, "deep_sleep": True, "listen_pause": 0.7, "listen_max": 10, "notif_muted": [], "phone_calls": True, "email_cloud": False, "mobile": False, "phone_quiet": True, "phone_notes_cloud": False, "suggest": False,
         "reaction_sounds": True, "power_ok": False, "reasoning": "auto", "live_3d": True,
         "react_to_sound": True, "avatar_roam": "corner", "presence": False, "presence_lock": False, "error_help": True,
         "skill_screen_checks": False, "skill_teach": True, "barge_in": True, "character": characters.DEFAULT}
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

PERSONA = f"""{{INTRO}}

How you talk (very important):
{{TALK}}- Every answer is fresh and about what the user just asked. Never repeat a sentence you said before in this chat.
- Style never replaces facts: for the time, battery, internet, files or anything else on the laptop, call the tool
  first - every time, even if you answered it a minute ago - and say exactly what it returned. Never reuse a number, a time or a fact from an earlier answer - check again.
- Keep it to one or two short sentences, under 25 words - you are speaking, not writing.
  Don't repeat the user's words back to them. Jokes: one short line and the punchline.
{{TOUCH}}

How you work:
- Use your tools to actually do things on the laptop instead of explaining how. Use several tools in a row when it helps.
- Replies are spoken aloud, so keep them short (1-2 sentences, under 35 words), natural, and free of markdown,
  tables or long lists unless the user asks for detail. Answer what was asked; don't tack on extra offers.
- Only mention being tired or the user working late when it's really true (battery under 20%, or after 11 PM).
- You have full access to this laptop: windows, keyboard, clipboard, files, apps (winget), Windows settings and
  PowerShell (as_admin for system changes).
- Work like a person sitting at this laptop: do the job in the real apps on screen, so the user can watch you.
  Research, looking things up, docs, GitHub, Stack Overflow: research_in_background - it Googles and reads the pages
  in Chrome windows parked off-screen, so the user keeps their screen and keyboard while you work, and you tell them
  what you found when it's done. Only when they want to watch or browse ("show me", "on my screen") use
  web_research, click_element and read_page in front of them. Code and git: VS Code.
  Writing: write_document. Files: list_folder / read_text_file, and open_path so the user sees the folder or file.
  Windows: window_action and close_all_windows - you walk to each window's own button and click it, so the user
  watches. Command-line jobs with no app or button (git log, ssh, npm, pip...): run_in_terminal - it runs in your own
  terminal window that stays minimized, so the user keeps working (show=true only if they ask to watch it). Questions about the user's own files or code (the last commit, what changed) are answered from
  the laptop with these tools - never with research_in_background, which is for the web. Use run_powershell (hidden) only as a last resort, for system jobs nothing on screen can do -
  never to fetch web pages, search the web, do git, or list/read files.
- Need an app for the job (Zoom, Notion, OBS, Postman...)? Call find_app first. If it's installed, open_app it and
  work in it. If it's missing, tell the user and ask in one sentence whether to install it - only on a yes call
  install_app with the Id find_app gave. If they say no, use the website in Chrome instead.
- Forms and logins (the safe way): fill_form types the user's saved details (name, email, phone, address...) into
  a form - you never see the values - and never submits; ask them to check it before you press Submit. To sign in,
  open the site and call help_login: you click the login box, their password manager fills it and they sign in.
  When a site asks for an OTP / verification code, call wait_for_code and let them type it. NEVER ask the user for a
  password, PIN, OTP or card number, never type one (type_text refuses password boxes), and never put one in a
  note, skill or memory. Never press Pay / Place order / Submit on a form without the user's yes.
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
- When you are not sure what the user means, which thing they mean, or how to do it, ask ONE short question
  instead of guessing (e.g. "Which file - the report or the notes?"). Never pretend to know or to have done it.
- If the user says you didn't do something ("you're not dancing"), believe them: do it now with the tool, or say
  honestly that you can't. Never insist that you did.
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
  Everything you write is spoken aloud, so never write stage directions like [react: "laugh"], *laughs* or
  *(I'm dancing)* - call react instead.
  If they ask for a move you don't have, ask whether to invent it (new_move) or do one you know.
- You have skills: routines the user taught you, saved as files. When one clearly fits a request, its name is at
  the end of these instructions - use run_skill; if the request is only a question or merely similar, don't run it.
  When the user teaches you something to do on a phrase ("when I say movie time, do X, Y and Z"), save it with
  save_skill. When the user corrects a skill ("no, also unmute"), change just that step with edit_skill - never
  retype the whole skill. "Remember that" after you finished a task: save_task_as_skill. After a skill runs, report
  only what the result says happened.
- To move a window to a side or corner of the screen, use drag_window - you carry it there yourself.
- For the time or date, call get_datetime. Never read out or repeat the [open windows ...] line.
- You are also the user's co-worker. To draft an email use draft_email, for a document, letter or notes use
  write_document, and to research something use research_in_background: they run in the BACKGROUND while the user
  keeps working (several can run at once) - don't open Gmail, Notepad or Chrome for them yourself.
  Say in one short sentence that you're on it; you'll tell them when it's done, and "show it" = show_finished_work.
- When the user wants you to sleep, rest, stop listening or says goodnight (speech recognition may garble it, e.g.
  "got to sleep", "go too sleep"), call go_to_sleep - saying goodnight alone does NOT stop you listening.
- "Read my notifications" / "any new messages?" = read_notifications. Summarise them briefly; never read out
  one-time codes, OTPs or passwords. "Don't read notifications from <app>" = mute_notifications (mute true);
  "resume / start reading <app> notifications" = mute_notifications (mute false).
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
# Her character (Settings -> Nova -> "Who should I be?"): who she is and how she talks come from her own folder,
# characters/<id>/persona.md (characters.py); the rules about tools and facts are the same for all of them.
# {NAME} becomes her name; {DESI_TALK} the Hindi/English talking rules, which depend on the voice setting.


def persona_text(cid=None):
    """Her instructions for the chosen character (settings "character")."""
    ch = characters.get(cid or settings().get("character"))
    p, name = ch["persona"], ch["name"]
    fill = lambda t: (t or "").replace("{NAME}", name).replace("{DESI_TALK}", (TALK_ENGLISH if voice.ENGLISH_ONLY else TALK_MIXED).strip())
    intro = fill(p.get("intro")) or f"You are {name}, who lives inside the user's Windows laptop and looks after it."
    intro += (f'\nYour name is {name} - always. When asked who you are, say "{name}". The user wakes you by '
              f'saying "Hey {name}".')
    talk = fill(p.get("talk")).rstrip() + "\n" if p.get("talk") else ""
    return (PERSONA.replace("{INTRO}", intro).replace("{TALK}", talk).replace("{TOUCH}", fill(p.get("touch")))
            .replace("{HEAR}", "" if voice.ENGLISH_ONLY else HEAR_MIXED))


def wake_phrase():
    """What the user says to call her: "Hey Lucy" for Lucy."""
    return f'Hey {characters.get(settings().get("character"))["name"]}'


# How hard her brain thinks first (settings "reasoning"): low / high / max, or auto = low for everyday chat and
# quick actions, high for questions that need working out and for long or failing tasks. The same choice also turns
# a local thinking model's "think" on, so the backup brain follows it too.
REASONING_CHOICES = ("auto", *cloud.LEVELS)
HARD_ASK = re.compile(
    r"\b(why|explain|compare|plan|analy[sz]e|reason|step[- ]by[- ]step|think (it|this) through|figure out|calculate|"
    r"solve|debug|code|script|program|research|draft|essay|summari[sz]e|pros and cons|best way|strategy|"
    r"difference between|optimi[sz]e|troubleshoot|math|proof|puzzle)\b|\bhow (do|does|can|should|would|to)\b", re.I)


def reasoning_level(s, task=None):
    pick = s.get("reasoning", "auto")
    if pick in cloud.LEVELS:
        return pick
    goal = (task or {}).get("goal", "")
    if task and (task.get("steps", 0) >= 3 or task.get("calls", 0) >= 4 or task.get("done") is False):   # a long task, or a check said "not done"
        return "high"
    # HARD_ASK holds the built-in words; the router's deep_triggers (config/model_routing.json, user-editable) count
    # too, so a word added there for local planning also makes a cloud brain think harder - one list to edit.
    return "high" if len(goal) > 160 or HARD_ASK.search(goal) or router.deep_wanted(goal) else "low"


def active_tools(model):
    """The tool schemas the brain sees this request. A local model gets the set picked for the request (toolpick.py:
    a shorter prompt makes the small model on the laptop GPU much faster); a cloud brain gets all of them."""
    task = state.get("task")
    off = {"escalate"} if cloud.parse(model) or not router.options(settings())["auto_route"] else set()
    if cloud.parse(model) or not task or not task.get("tools"):
        every = tools.TOOLS + mcp.tools_list()          # + 🔌 connector tools
        return [t for t in every if t["name"] not in {"more_tools"} | off] if cloud.parse(model) else [
            t for t in every if t["name"] not in off]
    return [t for t in toolpick.schemas(task["tools"]) if t["name"] not in off]


def system_prompt(model):
    # Who the user is + lessons from her own mistakes (data/knowledge.json), picked once per request for what it's
    # about. Fixed for the whole task, so Ollama can reuse the prompt it has already read between steps.
    task = state.get("task")
    facts = task["facts"] if task and "facts" in task else knowledge.prompt_block()
    proto = "" if llm.supports_tools(model) else llm.text_protocol(active_tools(model))
    # Without this she guesses her model from the chat history and repeats an old answer after a switch.
    c = cloud.parse(model)
    which = (f"{c[1]} ({cloud.PROVIDERS[c[0]]['label'].split()[0]}, in the cloud; if it fails, {settings()['model']} "
             "on this laptop takes over)") if c else f"{model} (running locally on this laptop via Ollama)"
    brain_line = (f"\n\nYour brain (the AI model you run on) right now is {which}. If asked which model you are, say "
                  "exactly this - it may differ from what you said earlier in this chat.")
    return persona_text() + facts + brain_line + proto


# ------------------------------------------------------------------ conversation state

state = {"messages": [], "pending": None}
lock = threading.Lock()
recorder.busy = lock.locked     # 👀 while she handles a request, the windows she opens aren't the user's steps


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
    "open_app": "Opened an app", "open_website": "Opened the browser", "web_research": "Searched Google in Chrome",
    "read_page": "Read the page", "find_app": "Checked if an app is installed", "open_path": "Opened a file/folder",
    "list_folder": "Looked inside a folder", "search_files": "Searched files", "find_large_files": "Hunted big files",
    "preview_organize_folder": "Planned a cleanup", "organize_folder": "Organized files",
    "create_folder": "Created a folder", "delete_to_recycle_bin": "Moved to Recycle Bin",
    "temp_files_size": "Measured temp files", "clean_temp_files": "Cleaned temp files",
    "empty_recycle_bin": "Emptied Recycle Bin", "set_volume": "Adjusted volume", "media_control": "Controlled media",
    "set_brightness": "Adjusted brightness", "lock_screen": "Locked the screen", "power_action": "Power action",
    "run_powershell": "Ran a command", "run_in_terminal": "Typed a command in the terminal", "add_note": "Saved a note", "list_notes": "Read notes",
    "set_reminder": "Set a reminder", "list_reminders": "Checked your reminders", "remember_fact": "Remembered something", "forget_fact": "Forgot something",
    "list_windows": "Looked at open windows", "window_action": "Managed a window", "minimize_all_windows": "Showed the desktop", "close_all_windows": "Closed all windows",
    "type_text": "Typed text", "press_keys": "Pressed keys", "get_clipboard": "Read the clipboard", "read_selection": "Read the selected text",
    "set_clipboard": "Copied to clipboard", "take_screenshot": "Took a screenshot", "read_text_file": "Read a file",
    "write_text_file": "Wrote a file", "move_or_rename": "Moved/renamed", "copy_path": "Copied files",
    "search_installable_apps": "Searched for apps", "install_app": "Installed an app", "uninstall_app": "Uninstalled an app",
    "list_installed_apps": "Listed installed apps", "list_startup_apps": "Checked startup apps",
    "open_settings_page": "Opened Settings", "set_dark_mode": "Changed theme", "set_wallpaper": "Changed wallpaper",
    "toggle_wifi": "Switched Wi-Fi", "list_clickable": "Looked at the buttons", "click_element": "Clicked a button",
    "click_at": "Clicked on screen", "look_through_camera": "Looked through the camera",
    "click_nth": "Clicked an item", "scroll": "Scrolled", "fill_in": "Typed into a box",
    "fill_form": "Filled in the form", "help_login": "Helped you sign in", "wait_for_code": "Waited for your code",
    "video_control": "Pressed a video button",
    "look_at_screen": "Looked at your screen",
    "update_profile": "Updated your profile", "learn_lesson": "Saved a lesson", "recall_knowledge": "Checked my knowledge bank",
    "react": "Acted it out", "new_move": "Made up a new move", "forget_move": "Forgot a move",
    "change_look": "Changed my look", "drag_window": "Moved a window", "more_tools": "Found more tools", "escalate": "Switched brain",
    "read_notifications": "Read your notifications", "check_email": "Looked in your Gmail", "connect_gmail": "Opened the Gmail sign-in", "read_email": "Read an email", "mute_notifications": "Changed which apps' notifications I read", "draft_email": "Started drafting an email",
    "write_document": "Started writing a document", "research_in_background": "Started researching in the background", "show_finished_work": "Showed finished work",
    "background_jobs": "Checked background jobs", "stop_background_job": "Stopped a background job",
    "go_to_sleep": "Went to sleep", "run_skill": "Ran a skill", "save_skill": "Learned a new skill",
    "list_skills": "Listed my skills", "delete_skill": "Deleted a skill", "edit_skill": "Changed a skill",
    "undo_skill_change": "Undid a skill change", "save_task_as_skill": "Saved a skill", "login_if_needed": "Checked your sign-in",
    "start_watching": "Started watching", "stop_watching": "Watched you",
}

ASK = {
    "kill_process": "Close a program", "organize_folder": "Move files into category folders",
    "delete_to_recycle_bin": "Move something to the Recycle Bin", "clean_temp_files": "Delete old temp files",
    "empty_recycle_bin": "Permanently empty the Recycle Bin", "power_action": "Change power state", "close_all_windows": "Close all windows",
    "run_powershell": "Run a PowerShell command", "run_in_terminal": "Type a command in the terminal",
    "window_action": "Control a window", "type_text": "Type on your keyboard",
    "press_keys": "Press keys", "write_text_file": "Write a file", "move_or_rename": "Move/rename",
    "install_app": "Install an app", "uninstall_app": "Uninstall an app", "set_wallpaper": "Change your wallpaper",
    "toggle_wifi": "Switch Wi-Fi (admin)", "click_element": "Click something", "click_at": "Click on the screen",
    "click_nth": "Click an item", "fill_in": "Type into a box", "fill_form": "Fill in the form with your saved details", "delete_skill": "Delete a saved skill",
    "run_skill": "Run a skill", "login_if_needed": "Sign in on a website",
}


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
    return ask_label(c["name"])


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
    if not last or time.time() - last["ts"] > 180 or not CORRECTION_RE.search(text or ""):
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
    s = settings()
    if name == "power_action" and args.get("action") == "cancel":
        return False
    if name == "run_skill" and skill_needs_ok(args):    # it only sounds like that skill (or the brain picked it): ask
        return True
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
                calls += [{"name": t, "args": {}} for t in (st.get("tools") or SKILL_BRAIN_TOOLS)]
        return any(is_risky(c) for c in calls)
    if mode == "trusted":
        return tools.is_dangerous(name, args)
    return name in tools.RISKY


# ------------------------------------------------------------------ look -> act -> check
# A person looks at the screen after every click to see what happened. Nova does the same: after each on-screen
# action she pauses, looks, and is told what the screen shows now and whether the goal is finished. That keeps her
# on track through tasks with many steps, and stops her saying "done" when it isn't.

ACT_TOOLS = {"open_app", "open_website", "web_research", "open_path", "window_action", "minimize_all_windows", "type_text",
             "close_all_windows", "run_in_terminal", "press_keys", "fill_in", "click_element", "click_nth", "click_at", "scroll", "video_control", "drag_window"}
SLOW_TOOLS = {"open_app", "open_website", "web_research", "open_path"}   # these need a moment before the screen settles
LOOK_FROM_STEP = 2      # one-step requests ("open Chrome") stay quick: only the window list is checked
MAX_TURNS, MAX_TASK_TURNS = 10, 24
# A cloud brain on High/Max reasoning plans its steps well, so a big job (find my GitHub username, then set up the
# repo...) gets more steps before she pauses; 24 is a guard against a small model going round in circles.
DEEP_TASK_TURNS = 50
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


llm.log = task_log                      # the brain notes oddities (a call written as text) in the same log


# ------------------------------------------------------------------ her thoughts, live: the chat's "💭 thinking" log
# and the thought cloud over 3D Nova. Every step she takes (and a thinking model's own reasoning) is noted here while
# a request runs. In memory only, like the chat itself; the task log keeps its usual copy.

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
        thoughts["list"].append({"id": thoughts["next"], "turn": thoughts["turn"], "t": round(time.time(), 2),
                                 "text": text})
        thoughts["next"] += 1
        del thoughts["list"][:-MAX_THOUGHTS]
    task_log(f"THINK: {text}")
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


threading.Thread(target=_cloud_worker, daemon=True).start()


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


# ------------------------------------------------------------------ the Stop switch
# Pressing Stop (chat page, Esc, or the tray menu) sets stop_flag. She checks it before every brain turn and every
# tool, and slow waits (her brain thinking, a screen look) are watched so Stop never has to wait for them to finish.

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
    threading.Thread(target=run, daemon=True).start()
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
    busy = lock.locked()
    if busy:
        stop_flag.set()
    mouth.stop()
    if busy:
        think("🛑 You pressed Stop - dropping everything.", cloud_too=False)
    thoughts_done()
    task_log(f"STOP pressed ({'while working' if busy else 'while idle'})")
    return busy


def new_turn():
    """Thoughts from here on belong to a new request (the chat groups them per request)."""
    with _thought_lock:
        thoughts["turn"] += 1


# "keep going" after she paused a long task: carry on with that task, don't start a new one (a skill matched
# "keep going" to work-mode once and opened VS Code and Gmail instead).
CONTINUE_RE = re.compile(r"^\W*(please\s+)?(ok(ay)?\W+)?(keep going|carry on|continue|go on|resume|proceed|"
                         r"finish (it|that|the task)|jaari rakho|chalu rakho|aage badho|karte raho)\b", re.I)


EMAIL_WORDS = re.compile(r"\b(e-?mails?|mails?|gmail|inbox|unread|otp|verification code)\b", re.I)    # her mail stays local


def new_task(goal):
    """Start a request. Returns a note to add to the user's message (only when a paused task is resumed)."""
    task_log(f"\n=== {time.strftime('%Y-%m-%d')} REQUEST: {goal[:300]}")
    new_turn()
    state.pop("react_after", None)
    state.pop("skill_final", None)
    state.pop("skill_pause", None)
    clean = _unspoken(goal)[:300]
    last = state.get("task") or {}
    if any(x.get("ok") and x["tool"] not in skills.SKIP_IN_TRACE for x in last.get("trace", [])):   # for "remember that"
        state["last_trace"] = {"goal": last.get("goal", ""), "trace": list(last["trace"])}
    if last.get("paused") and CONTINUE_RE.search(clean):
        last["paused"] = False
        task_log(f"RESUMING paused task: {last['goal'][:200]}")
        think(f"▶️ Carrying on with: {last['goal'][:120]}", cloud_too=False)
        return ("\n[note from Nova's system] You paused this task after many steps and the user wants you to carry "
                f"on: {last['goal']}\nContinue from where you stopped. Don't redo steps that already worked.")
    skill = how = None
    try:
        skill, how = skills.pick(clean)       # exact (it said the skill), fuzzy (it only sounds like it) or neither
        task_log(skills.last_decision)
        keep = (list(last.get("used", ())) + (skill["tools"] if skill and how == "exact" else [])
                + (["run_skill"] if skills.load() else []))       # her skill names are in the prompt, so is run_skill
        picked = toolpick.pick(clean, keep=keep)
        named = watch_note()                 # right after "watch me ... done": the next words are probably its name
        if named:
            keep.append("save_task_as_skill")
        picked = toolpick.pick(clean, keep=keep) if named else picked
        facts = (knowledge.prompt_block(clean) + skills.prompt_block(clean, skill, how or "exact") + skill_fix_note(clean)
                 + named)
        if skill:
            think(f"📜 This sounds like my '{skill['name']}' skill." if how == "exact"
                  else f"📜 This might be my '{skill['name']}' skill - I'll check with you first.", cloud_too=False)
    except Exception as e:      # never let picking stop her answering: all tools, the plain summary
        task_log(f"PICK failed: {type(e).__name__}: {e}")
        skill = how = None
        picked, facts = None, knowledge.prompt_block()
    state["task"] = {"goal": clean, "start": len(state["messages"]), "steps": 0, "did": [], "done": None,
                     "screen": "", "nudged": False, "tools": picked, "facts": facts, "used": [],
                     "skill": skill["name"] if skill else "", "skill_how": how or ""}
    s = settings()
    if cloud.parse(brain(s)) and llm.cloud_resting():   # the cloud failed a moment ago: this whole task runs locally
        state["task"]["model"] = s["model"]
        task_log(f"CLOUD RESTING -> local {s['model']} for this task")
    if cloud.parse(brain(s)) and not s.get("email_cloud") and not state["task"].get("model") and EMAIL_WORDS.search(clean):
        state["task"]["model"] = s["model"]      # her mail never goes to a cloud brain unless ⚙ allows it
        task_log(f"EMAIL: local {s['model']} for this task")
    if not cloud.parse(task_brain(s)):
        choose_local_route(state["task"], s)
    task_log(f"PICKED {len(picked) if picked else 'all'} tools ({retrieval.status()}): "
             + (", ".join(n for n in picked if n not in toolpick.CORE) if picked else "-"))
    return ""


def task_brain(s, task=None):
    """The brain this task is really on: the one it was switched to (local fallback, coder, deep...) or the chosen one."""
    task = task if task is not None else state.get("task")
    return (task or {}).get("model") or brain(s)


def choose_local_route(task, s):
    """Pick the local helper brain from the request's words (brain/router.py); _agent_loop acts on it next turn."""
    if task and router.options(s)["auto_route"] and not task.get("routed"):
        goal = task.get("goal", "")
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
CODER_NOTE = ("\n\nYOU ARE THE CODING BRAIN for this task. This laptop runs Windows: run Python as `python` (never "
              "`python3` - on Windows that opens the Microsoft Store instead), and commands run in PowerShell. If the "
              "user only asks what is wrong with some code, what an error means or how to write something, answer "
              "straight away from what you know - run nothing. Run commands only when the job needs this laptop "
              "(run, change, install or inspect something here). A command that fails is not proof that something "
              "is missing: say what failed instead of guessing.")

CODER_QUESTION_NOTE = ("\n\nTHIS REQUEST IS A QUESTION. You have no tools for it: answer now in plain words - what is "
                       "wrong and how to fix it, with a few lines of corrected code if that helps. Keep it short.")


def route_task(kind, why=""):
    """Give this task a bigger local brain (brain/router.py). Returns the text the everyday brain is told."""
    task, s = state.get("task"), settings()
    if not task or cloud.parse(task_brain(s, task)):
        return "No other brain to switch to - carry on yourself."
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
        think(f"🔀 Code job - switching to my coding brain ({m}). Loading it can take a minute…")
        task_log(f"ROUTE code -> {m} ({why[:100]})")
        return f"You are now the coding brain ({m}). Carry on with the task: {task['goal']}"
    if task.get("plan"):
        return "You already have the plan (see your instructions). Follow it."
    think("🧠 Complex job - asking my deep-thinking brain to plan it first. Loading it can take a minute…")
    t0 = time.time()
    ts = ThoughtStream()
    plan, m = interruptible(router.make_plan, s, task["goal"], on_think=unless_stopped(ts.feed))
    ts.flush()
    if not plan:
        task_log(f"ROUTE deep failed: {m}")
        think(f"🧠 Couldn't get a plan ({m}) - I'll work it out myself.", cloud_too=False)
        return f"No plan available ({m}). Work it out yourself, one step at a time."
    task_log(f"ROUTE deep -> plan from {m} in {time.time() - t0:.0f}s:\n      " + plan.replace("\n", "\n      "))
    task["plan"] = plan
    task["facts"] = (task.get("facts", "") + "\n\nPLAN for this task, written by your deep-thinking brain - "
                     "follow it step by step:\n" + plan + "\nThe plan is advice from another brain: it may name tools "
                     "you don't have or add silly or unsafe steps. Use only your real tools, skip steps that don't "
                     "help, never delete or change anything the user hasn't agreed to, and stop when the goal is reached.")
    if router.deep_can_run(s):
        task["model"] = m
        think(f"🧠 Plan ready ({time.time() - t0:.0f}s) - the deep brain carries it out.")
        return f"You are now the deep brain ({m}). Follow the plan in your instructions."
    heavy = router.heavy_executor(s, plan)
    if heavy:
        task["model"] = heavy
        think(f"🧠 Plan ready ({time.time() - t0:.0f}s), {router.step_count(plan)} steps - that's a big job, so my "
              f"stronger tool brain ({heavy}) carries it out.")
        task_log(f"ROUTE long plan ({router.step_count(plan)} steps) -> {heavy}")
        return f"You are now the stronger brain ({heavy}). Follow the plan in your instructions step by step with your tools."
    think(f"🧠 Plan ready ({time.time() - t0:.0f}s) - now I'll carry it out step by step.")
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
    cfg = settings()
    sch = toolpick.schemas([n for n in names if n not in skills.LOCKED])
    proto = "" if llm.supports_tools(model) or not sch else llm.text_protocol(sch)
    sofar = "; ".join(f"{k}: {v}" for k, v in list(run["results"].items())[-6:]) or "nothing yet"
    sysmsg = (f"You are Nova, doing ONE step of your skill '{s['name']}'. Do exactly this and nothing more: {instruction}\n"
              f"What the skill has done so far: {sofar}\nCall the tools you need, one at a time. When the step is done "
              "answer in ONE short sentence saying what you did or saw. If it can't be done, or there is nothing to "
              "do, start your answer with STOP: and say why. Never type or ask for a password or a code." + proto)
    msgs = [{"role": "system", "content": sysmsg}, {"role": "user", "content": "Go."}]
    text, called = "", 0
    for _ in range(SKILL_BRAIN_TURNS):
        check_stop()
        stored, text, calls = interruptible(llm.chat, model, msgs, sch, fallback=cfg["model"] if cloud.parse(model) else None)
        msgs.append(stored)
        if not calls:
            break
        called += len(calls)
        msgs.extend(execute(calls, approved, activity, model))
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
        seen = interruptible(llm.see, img, CHECK_PROMPT.format(goal=s["description"] or s["name"], step=step,
                                                              result=out[:200], windows=tools.open_windows_summary(4)),
                             preferred=settings()["model"], kind="check")
    except Stopped:
        raise
    except Exception as e:
        return True, f"(couldn't look: {e})"
    m = re.search(r"STEP WORKED:\s*(yes|no)", seen, re.I)
    return bool(m and m.group(1).lower() == "yes"), (re.search(r"SCREEN:\s*(.+)", seen) or [None, seen])[1]


def _skill_say(run, text):
    run["said"].append(_sfill(text, run))


def _pause_skill(s, run, question, resume_at):
    """The skill asks the user something: stop here, keep where we are, and let _agent_loop / confirm_pending ask."""
    state["skill_run"] = run
    state["skill_pause"] = {"name": s["name"], "question": question, "resume_at": resume_at, "input": run["input"]}
    return f"Paused my '{s['name']}' skill to ask the user: {question}"


def _skill_report(run, instruction, model):
    """The last `report` step: the brain words the answer from what the steps found."""
    found = "\n".join(f"- {k}: {v}" for k, v in run["report"][-8:])[:3000]
    msgs = [{"role": "system", "content": "You are Nova. Answer the user briefly and warmly from the results below. "
                                          "Use only what the results say."},
            {"role": "user", "content": f"{instruction}\n\nResults:\n{found}"}]
    try:
        _, text, _ = interruptible(llm.chat, model, msgs, [], fallback=settings()["model"] if cloud.parse(model) else None,
                                   num_predict=220)
        return (text or "").strip()
    except Stopped:
        raise
    except Exception as e:
        task_log(f"SKILL REPORT failed: {type(e).__name__}: {e}")
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
    task = state.get("task")
    activity.append(FRIENDLY["run_skill"])
    if s["kind"] == "recipe":
        think(f"📜 Following my '{s['name']}' skill.")
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
        run = state.get("skill_run")
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
        think(f"📜 Running my '{s['name']}' skill ({len(steps)} steps).")
        i = 0
    watch = _can_teach(s) and not run.get("taught") and not recorder.recording()
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
        check_stop()
        st = steps[i]
        sid = st["id"]
        if watch and last_done and recorder.user_acted_since(t_mark):
            return _takeover_pause(s, run, last_done, sid)
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
        think(f"📜 Step {sid}: " + (st.get("tool") or "ask my brain") + (f" ({_short_args(st['args'])})" if st.get("args") else ""))
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
            if is_risky(call) and not approved:
                out, ok_ = "Not done - it needs the user's OK.", False
            else:
                out = tools.run_tool(call["name"], call["args"])
                ok_ = not _failed(out)
                activity.append(FRIENDLY.get(call["name"], call["name"]))
                if task is not None and call["name"] not in task.setdefault("used", []):
                    task["used"].append(call["name"])
                if call["name"] in ACT_TOOLS and ok_:
                    settle(call["name"], quick=True)      # the next step needs the window this one opened
            label = call["name"]
            task_log(f"  SKILL STEP {sid} {call['name']} {json.dumps(call['args'], ensure_ascii=False)[:200]} -> {out[:200]}")
        ctx["result"] = out
        if ok_ and st.get("expect"):                # the code checks the result itself; the tool saying "ok" isn't enough
            ok_, seen = checks.evaluate(st["expect"], ctx)
            task_log(f"  SKILL CHECK {sid} {skills.render_check(st['expect'])} -> {'ok' if ok_ else 'NOT ok'} ({seen})")
            if not ok_:
                out = f"{_short(out, 80)} - but the check '{skills.render_check(st['expect'])}' failed: {seen}"
        elif (ok_ and "tool" in st and st["tool"] in ACT_TOOLS and settings().get("skill_screen_checks")
              and tools._screen_allowed()):
            ok_, seen = skill_screen_ok(s, f"{st['tool']} {_short_args(call['args'])}", out)
            task_log(f"  SKILL SCREEN CHECK {sid} -> {'ok' if ok_ else 'NOT ok'} ({seen[:120]})")
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
                    think(f"🔁 Step {sid} didn't work - trying again.", cloud_too=False)
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
        return _takeover_pause(s, run, last_done, "")
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
    state["last_skill"] = {"name": s["name"], "ts": time.time(), "result": "worked" if ok else f"failed at {first[0] or 'a step'}"}
    if run["stopped"] and run["failed"] and not run.get("taught") and _can_teach(s) and skills.get(s["name"]):
        sid = run["failed"][-1][0]           # "show me how to do that step" - then the skill carries on after it
        ids = [x["id"] for x in skills._ids_for([dict(y) for y in s["steps"]])]
        nxt = ids[ids.index(sid) + 1] if sid in ids and ids.index(sid) + 1 < len(ids) else ""
        state["skill_run"] = run
        state["skill_pause"] = {"name": s["name"], "input": run["input"], "resume_at": nxt,
                                "question": f"{final} Want to show me how to do step {sid}? Say yes, do it yourself, then say 'done'.",
                                "teach": {"phase": "show", "sid": sid, "mode": "replace"}}
        task_log(f"SKILL DONE {s['name']} ok=False - offering to learn step {sid} from the user")
        return f"Paused my '{s['name']}' skill: step {sid} failed, asking the user to show it."
    state.pop("skill_run", None)
    state["skill_final"] = final
    task_log(f"SKILL DONE {s['name']} ok={ok} ran={len(run['ok'])} failed={n_failed} :: {final[:200]}")
    lines = [f"{sid}: {txt}" for sid, txt in run["results"].items()]
    return (f"Ran the skill '{s['name']}'" + (f" - {n_failed} step(s) didn't work" if n_failed else "") + ":\n"
            + "\n".join(lines) + f"\nTell the user exactly this: {final}\nDon't redo any of these steps.")


def resume_skill(args, approved, activity):
    """The user answered a question a skill asked. Returns the answer dict (like _agent_loop's result)."""
    model = brain()
    s = skills.get(args.get("name", ""))
    teach = args.get("teach") or {}
    name = args.get("name", "")
    if teach.get("phase") == "show":         # "want to show me how it should go?"
        run = state.get("skill_run") or {}
        if not approved:
            state.pop("skill_run", None)
            if not run.get("counted"):
                skills.record_run(name, False, stopped=True)
            return _say_final(f"Okay, I stopped my '{name}' skill there.", activity)
        state["teach"] = {"skill": name, "sid": teach.get("sid", ""), "mode": teach.get("mode", "insert"),
                          "resume_at": args.get("resume_at", ""), "input": args.get("input", "")}
        msg = start_watching_now(f"step {teach.get('sid', '')} of my '{name}' skill")
        if recorder.recording():
            return _say_final(msg, activity)
        state.pop("teach", None)             # couldn't watch: carry on with the skill instead
        args = {"name": name, "input": args.get("input", ""), "resume_at": args.get("resume_at", "")}
        approved = True
    elif teach.get("phase") == "apply":      # "change my skill like this?"
        msg = (skills.edit(name, teach.get("ops", []), author="recorder", cause="shown by the user") if approved
               else "Okay, I left the skill as it was.")
        task_log(f"SKILL TAUGHT {name}: {msg[:200]}")
        if not state.get("skill_run"):
            return _say_final(msg, activity)
        out = run_skill({"name": name, "input": args.get("input", ""), "resume_at": args.get("resume_at", "")},
                        True, activity, model)
        pause = state.pop("skill_pause", None)
        if pause:
            pause["question"] = msg + " " + pause["question"]
            return ask_skill_question(pause)
        return _say_final(msg + " " + (state.pop("skill_final", None) or out), activity)
    if not approved:
        run = state.pop("skill_run", None) or {}
        skills.record_run(args.get("name", ""), False, stopped=True)
        final = f"Okay, I stopped my '{args.get('name', '')}' skill there."
    else:
        out = run_skill(args, True, activity, model)
        pause = state.pop("skill_pause", None)
        if pause:
            return ask_skill_question(pause)
        final = state.pop("skill_final", None) or out
    state.pop("skill_final", None)
    state["messages"].append({"role": "assistant", "content": final})
    return {"reply": final, "activity": activity, "say": final}


def ask_skill_question(pause, append=True):
    """Put a skill's own question to the user as an Allow / No box. append=False: the caller adds the message."""
    args = {"name": pause["name"], "input": pause["input"], "resume_at": pause["resume_at"]}
    if pause.get("teach"):
        args["teach"] = pause["teach"]
    state["pending"] = [{"id": "skill-resume", "name": "run_skill", "args": args}]
    if append:
        state["messages"].append({"role": "assistant", "content": pause["question"]})
    return {"reply": pause["question"], "activity": [], "say": pause["question"],
            "pending": [{"tool": "run_skill", "label": pause["question"], "input": args}]}


def settle(tool_name, quick=False):
    """Wait until the screen stops changing after an action (a page that is still loading keeps renaming its
    window), so the check looks at the result and not at a half-loaded page. Returns the window list.
    quick: only the window list is needed next (no screenshot), so a shorter wait is enough."""
    time.sleep((1.2 if tool_name in SLOW_TOOLS else 0.5) if quick else (1.8 if tool_name in SLOW_TOOLS else 1.0))
    last, same, end = None, 0, time.time() + 5
    while True:
        check_stop()
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
    think("👀 Looking at the screen to check that worked…")
    img = tools._grab_screen_jpeg()   # in memory only, like every screen look
    seen = interruptible(llm.see, img, CHECK_PROMPT.format(goal=task["goal"], step=step, result=result[:200], windows=windows),
                   preferred=model, kind="check")
    state["check_image"] = img        # the chat shows the last thing she looked at
    done = re.search(r"TASK DONE:\s*(yes|no)", seen, re.I)
    task["done"] = (done.group(1).lower() == "yes") if done else None
    task["screen"] = seen
    what = re.search(r"SCREEN:\s*(.+)", seen)
    think(("✅ The screen says it's done." if task["done"] else "❌ Not finished yet." if task["done"] is False
           else "🤔 Can't tell from the screen.") + (f" I see: {what.group(1).strip()}" if what else ""))
    activity.append("Checked the screen")
    return seen


def _big_brain(model):
    """A cloud brain, or a bigger local one the router switched to: trusted to follow the window list, so the slow
    screen look (which would reload the small vision model every step and push the big model off the GPU) runs only
    once, when it says it has finished."""
    return bool(cloud.parse(model)) or model != settings()["model"]


def check_step(call, out, activity, model):
    """After an on-screen action: wait for the screen to settle, look, and add what Nova sees to the tool result.
    A cloud brain is trusted to follow the window list step by step; the (slow, ~10 s) screen look runs once, when
    it says it has finished (final_check). The small local model needs the look after every step."""
    task = state.get("task")
    if not task or out.startswith("Error"):
        return out
    task["steps"] += 1
    task["did"].append(f"{call['name']} {json.dumps(call['args'], ensure_ascii=False)[:80]}")
    on_cloud = _big_brain(model)
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
        try:
            out = _execute_one(c, approved, activity, model)
        except Stopped:      # every call still gets a result, so the history stays valid for the next request
            out = "The user pressed Stop before this step finished. It was not done (or only partly)."
        task_log(f"TOOL {c['name']} {json.dumps(c['args'], ensure_ascii=False)[:300]}\n   -> " + out.replace("\n", "\n      "))
        outs.append(out)
        _trace(c, out)
    state["last_outs"] = outs
    return [llm.tool_result_message(model, c, out) for c, out in zip(calls, outs)]


def _trace(c, out):
    """Remember what this task really did (tool, arguments, whether it worked): "remember that" turns it into a skill."""
    task = state.get("task")
    if task is None or c["name"] in skills.LOCKED:
        return
    task.setdefault("trace", []).append({"tool": c["name"], "args": dict(c["args"] or {}), "ok": not _failed(out),
                                         "out": _short(out), "t": time.time()})
    del task["trace"][:-60]


def skill_hints(goal, steps):
    """Optional help from the cloud brain when a task becomes a skill: which typed words should be {input}, and what
    to check after a step. It only suggests; skills.draft_from_steps checks every suggestion. {} without a cloud brain."""
    s = settings()
    m = brain(s)
    if not cloud.parse(m) or llm.cloud_resting():
        return {}
    listing = "\n".join(f"{st['id']}. {skills.render_step(st)}" for st in steps)
    prompt = ("A voice assistant just did this task and will save its steps as a reusable skill.\n"
              f"Task: {goal[:300]}\nSteps:\n{listing}\n\nReply with ONLY a JSON object: "
              '{"slots": [{"step": "s2", "arg": "<argument name>", "find": "<the part of the argument that changes each time, e.g. a search word>"}], '
              '"expect": {"s2": "<check>"}, "not_for": ["<2-4 similar requests that must NOT start this skill>"]}. '
              'Checks you may use: window_open "text", front_window "text", page_has "text", fullscreen, '
              "dark_mode on. Use slots only for words the user would change next time. Leave a list empty if unsure.")
    try:
        _, text, _ = interruptible(llm.chat, m, [{"role": "user", "content": prompt}], [], fallback=None)
        found = re.search(r"\{.*\}", text or "", re.S)
        return json.loads(found.group(0)) if found else {}
    except Stopped:
        raise
    except Exception as e:
        task_log(f"SKILL HINTS failed: {type(e).__name__}: {e}")
        return {}


def save_task_as_skill_now(args):
    """"Remember that": turn the steps of the task that just finished into a draft skill."""
    last = state.get("last_trace")
    if not last:
        return "I haven't finished a task I could save yet - do it once, then say 'remember that'."
    steps, why = skills.steps_from_trace(last["trace"], last["goal"])
    if not steps:
        return why
    hints = skill_hints(last["goal"], steps)
    msg = skills.draft_from_steps(args.get("name", ""), args.get("description", ""), args.get("examples", ""), steps,
                                  hints, last["goal"])
    task_log(f"SKILL FROM TASK: {msg[:200]}")
    if msg.startswith("Saved"):
        last["from"] = "saved"               # done: the "what should I call it" note stops
    return msg


RESEARCH_CUE = re.compile(
    r"\b(research|look (it |that |this )?up|look into|find out|search|google|investigate|dig (in|into)|compare|"
    r"comparison|versus|vs\.?|latest|news|current|today'?s|prices?|reviews?|best|top \d+|sources?|report|"
    r"on the (web|internet)|online|in the background|deep dive)\b", re.I)


def _asked_to_research(task):
    """Did the user's request ask for research / looking something up? (Explaining a topic is not.)"""
    goal = (task or {}).get("goal") or ""
    return bool(RESEARCH_CUE.search(_unspoken(goal)))


WATCH_SEARCH_RE = re.compile(r"\b(show me|on (my|the) screen|in front of me|open (google|chrome)|let me (see|watch)|"
                             r"browse|in chrome|in the browser)\b", re.I)


def _execute_one(c, approved, activity, model):
    check_stop()
    if c["name"] == "web_research" and not WATCH_SEARCH_RE.search(_unspoken(((state.get("task") or {}).get("goal")) or "")):
        # The small brain picks the on-screen Google tool for plain "research X" and then makes up the answer
        # (it never reads a page). Plain research belongs in the background job, which really reads sources.
        q = (c.get("args") or {}).get("query") or ""
        c = {**c, "name": "research_in_background", "args": {"topic": q}}
    if is_risky(c) and not approved:
        out = "The user declined this action. Do not do it."
        activity.append(f"Skipped: {FRIENDLY.get(c['name'], ask_label(c['name']))}")
        think(f"🚫 Skipping: {ask_label(c['name']).lower()} (not allowed).")
    elif mcp.owns(c["name"]):                   # 🔌 a connector's tool runs in its MCP server
        think(f"🔌 Using the {mcp.server_of(c['name'])} connector: {c['name']}" + (
            f" ({_short_args(c['args'])})" if _short_args(c["args"]) else ""))
        out = mcp.call(c["name"], c["args"] or {})
        think("→ " + (out.strip().splitlines() or [""])[0][:160], cloud_too=False)
        activity.append(mcp.label(c["name"]))
        if state.get("task") is not None and c["name"] not in state["task"].setdefault("used", []):
            state["task"]["used"].append(c["name"])
    elif c["name"] == "run_skill":
        out = run_skill(c["args"] or {}, approved, activity, model)
    elif c["name"] == "save_task_as_skill":
        out = save_task_as_skill_now(c["args"] or {})
        activity.append("Saved a skill")
    elif c["name"] == "start_watching":
        out = start_watching_now(str((c["args"] or {}).get("goal", "")))
        activity.append("Started watching")
    elif c["name"] == "stop_watching":
        out = finish_watching() if recorder.recording() else "I wasn't watching."
        activity.append("Watched you")
    elif c["name"] == "react" and avatar_client.running():
        out = queue_reaction(c["args"] or {}, activity)
    elif c["name"] == "new_move" and avatar_client.running():
        out = queue_new_move(c["args"] or {}, activity)
    elif c["name"] == "research_in_background" and not _asked_to_research(state.get("task")):
        # 2026-11-18: she sent "explain how airplanes fly" and "tell me fun facts" to web research - 4 Chrome jobs piled
        # up, replies took 40-76 s and she kept interrupting with "research done". Plain questions are answered at once.
        out = ("Not run - the user did not ask you to research or look anything up. Answer from what you know "
               "in a few sentences now. If you're not sure of something, say so or ask them, don't guess.")
        think("🙅 That's a plain question - I'll just answer instead of researching.", cloud_too=False)
    elif c["name"] == "go_to_sleep":
        state["sleep_after"] = True         # she says goodnight first; heard()/chat() then really end the conversation
        activity.append(FRIENDLY["go_to_sleep"])
        think("😴 You want me to sleep - I'll nap right after this answer.")
        out = "Done: you'll go to sleep right after this answer. Just say a very short goodnight (a few words)."
    elif c["name"] == "escalate":
        a = c["args"] or {}
        out = route_task("code" if a.get("kind") == "code" else "deep", str(a.get("why", "")))
        activity.append("Switched brain")
    elif c["name"] == "more_tools":
        need = str((c["args"] or {}).get("need", ""))
        think(f"🧰 None of my tools fit - looking for one that can {need[:80]}")
        out = more_tools(state.get("task"), need)
        activity.append(FRIENDLY["more_tools"])
        think("🧰 " + out.split(". Use")[0], cloud_too=False)
    else:
        hint = _short_args(c["args"])
        think(f"🔧 Using {c['name']}" + (f" ({hint})" if hint else ""))
        tools.LAST_IMAGE["jpg_b64"] = None
        out = tools.run_tool(c["name"], c["args"])
        think("→ " + out.strip().split("\n")[0][:160], cloud_too=False)
        activity.append(FRIENDLY.get(c["name"], c["name"]))
        if state.get("task") is not None and c["name"] not in state["task"].setdefault("used", []):
            state["task"]["used"].append(c["name"])     # kept for the next request ("now close it")
        if tools.LAST_IMAGE["jpg_b64"]:  # show the user exactly what Nova looked at
            state.setdefault("images", []).append(tools.LAST_IMAGE["jpg_b64"])
            tools.LAST_IMAGE["jpg_b64"] = None
        if c["name"] in ACT_TOOLS:
            out = check_step(c, out, activity, model)
    return out


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
    CLOSED_STAGE = re.compile(shared.REACT_MARKER_RE.pattern + r"|\*\(.*?\)\*|\(\*.*?\*\)", re.I)
    OPEN_STAGE = re.compile(r"\[[^\]\n]*$|\*\((?:(?!\)\*)[^\n])*$|(?:^|\n)[ \t]*([*_])(?![*_\s])[^*_\n]*$")

    def __init__(self):
        self.buf = ""
        self.cut = False

    def feed(self, piece):
        if self.cut:                                              # everything after the cut is the echo: drop it
            return
        self.buf += piece
        if "[open windows" in self.buf:                           # she started echoing the hidden window list
            self.buf = INFO_ECHO.sub("", self.buf)
            if "[open windows" in self.buf:
                return                                            # wait for the closing bracket
        m = re.search(r"\[(?:check|spoken)", self.buf)
        if m:                                                     # junk the model adds after its answer (it repeats the
            self.buf, self.cut = self.buf[:m.start()], True       # request as "[spoken] ..."): stop there
        elif re.search(r"\[(?:c(?:h(?:e(?:c)?)?)?|s(?:p(?:o(?:k(?:e(?:n)?)?)?)?)?)?$", self.buf):
            return                                                # a half-written marker: wait for the rest
        if self.OPEN_STAGE.search(self.buf):
            return              # a half-written '[react: ...]' / '*(...)*' / '*italic line*': wait, so it isn't said
        self.buf = self.CLOSED_STAGE.sub(" ", self.buf)   # whole now: drop it before it's cut into sentences
        while True:
            m = self.END.search(self.buf)
            if not m:
                return
            sentence, self.buf = self.buf[:m.start()], self.buf[m.end():]
            speak(sentence.strip())

    def flush(self):
        self.buf = re.sub(r"\[(?:c(?:h(?:e(?:c)?)?)?|s(?:p(?:o(?:k(?:e(?:n)?)?)?)?)?)?$", "", self.buf)
        speak(INFO_ECHO.sub("", self.buf).split("[open windows")[0].strip())
        self.buf = ""


def agent_loop(activity):
    try:
        out = _agent_loop(activity)
    except Stopped:
        # Stopped while the brain was thinking or between steps: the history is complete up to here (every tool
        # call has its result), so just close it with a short note and give control back.
        msgs = state["messages"]
        if msgs and msgs[-1]["role"] != "assistant":
            msgs.append({"role": "assistant", "content": STOPPED_REPLY})
        think("🛑 Stopped.", cloud_too=False)
        activity.append("Stopped")
        out = {"reply": STOPPED_REPLY, "activity": activity, "say": STOPPED_REPLY, "stopped": True}
    nap["last_task"] = time.time()          # the 5-minute nap clock counts from when she finished, not from the ask
    out["images"] = state.pop("images", [])
    if state.get("check_image"):
        out["images"].append(state.pop("check_image"))
    return out


DONE_CLAIM_RE = re.compile(r"(all set|ready (for|to)\b|is ready|are ready|\bi'?ve (just )?(opened|started|turned|set|closed|pushed|sent|"
                           r"launched|enabled|switched|made|done|put|muted|played)|\b(opened|started|launched|enabled)\b[ !.]|\bdone[!.])", re.I)
PROMISE_RE = re.compile(
    r"\b(i'?ll|i will|i'?m|i am|let me|going to|now)\b[^.!?]{0,30}\b(click|open|search|play|scroll|type|press|clos|select|"
    r"tap|launch|start|go to|navigat|lock|mut|paus|turn|set|shut|restart|remind|delet|mov|install)"
    r"|\b(lock|open|clos|play|search|launch|start|turn|mut|paus|set|shut|restart|delet|mov|install|clean)\w*ing\b"
    r"[^.!?]{0,30}\b(now|right away)\b|\bi'?ll do (it|that) (right away|now)\b", re.I)
# "Reacting now... I'm dancing!" with no react call: her body never moved (2026-11-18 log).
BODY_CLAIM_RE = re.compile(r"\b(?:i'?m|i am|i'?ll|let me|watch me)\s+(?:now\s+|just\s+|still\s+)?(?P<v>dancing|dance|clapping|clap|"
                           r"spinning|spin|twirling|laughing|waving|bowing)\b|\breacting now\b|"
                           r"\bi (?:just )?(?:did a (?:little )?)?(?P<w>danced|clapped|spun|dance|spin|clap)\b", re.I)


REPEAT_LIMIT = 3      # the same tool call and result this many times in a row: she's told to stop; once more = the end
REPEATABLE = {"scroll", "press_keys", "type_text", "media_control", "video_control", "set_volume", "set_brightness",
              "click_at", "click_nth", "click_element", "react", "drag_window"}   # doing these again really does more


def _agent_loop(activity):
    """Run the model until it answers, or until it wants to do something risky."""
    s = settings()
    model = brain(s)
    spoken = []
    lead_ins = set()     # which of spoken were said just before a tool step ("Let me check:")
    nudged = did_something = claim_nudged = False
    last_calls = last_outs = None        # the same tool call, with the same result, again and again = stuck in a loop
    same_calls = 0
    unsaid = []      # reply text that has not been spoken yet (text spoken while it was being written isn't here)

    def result(reply, **extra):
        stage = shared.stage_reaction(shared.stage_directions(reply or "")[1])   # '[react: "laugh"]' -> after_turn plays it
        if stage:
            extra.setdefault("stage_reaction", stage)
        reply = re.sub(r"[ \t]{2,}", " ", shared.REACT_MARKER_RE.sub(" ", reply or "")).strip()   # ...and not on screen
        say = " ".join(unsaid).strip()
        if not say and not spoken:
            say = reply                     # e.g. the plain "Done!"
        return {"reply": reply, "activity": activity, "say": say, **extra}

    turns = 0
    while True:
        check_stop()
        task = state.get("task")
        if task and task.get("route") and not task.get("routed"):       # chosen from the request's words
            task["routed"] = True
            route_task(task["route"], "request wording")
        if task and task.get("model"):
            model = task["model"]
        deep = (bool(cloud.parse(model)) and reasoning_level(s, task) != "low") or bool(task and task.get("plan"))   # a planned job has many steps
        # Any tool step makes it a task - not just on-screen clicks. (Only those were counted, so a job done with
        # commands - git, PowerShell, looking at the screen - got the 10-turn limit of a plain question.)
        working = task and (task["steps"] or task.get("calls"))
        if turns >= ((DEEP_TASK_TURNS if deep else MAX_TASK_TURNS) if working else MAX_TURNS):
            break
        turns += 1
        nap["last_task"] = time.time()      # working on it: a long task is not "quiet", so no nap mid-job
        msgs = [{"role": "system", "content": system_prompt(model)}] + trimmed_history()
        # Ordinary questions are spoken while the answer is still being written. During a multi-step screen task
        # she must not talk early - her "done!" may still be corrected by her own check of the screen.
        stream = SpeechStream() if not (task and (task["steps"] or task.get("skill"))) and not state.get("remote") else None   # 📱: no voice
        think("💭 Working out what you need…" if turns == 1 and not did_something else "💭 Deciding the next step…")
        reasoning = ThoughtStream()
        level = reasoning_level(s, task)
        if level != "low" and cloud.parse(model):
            think(f"🧠 Thinking this through carefully ({level} reasoning)…", cloud_too=False)
        # If the cloud fails on a request the local router would hand to the coder or the planner, don't let the
        # everyday brain answer it: give up this turn and run it again on that brain (no local answer to throw away).
        local_route = local_route_for(task, s) if cloud.parse(model) else None
        t0 = time.time()
        try:
            answer_only = bool(task and task.get("answer_only") and model == task.get("model"))
            stored, text, calls = interruptible(llm.chat, model, msgs, [] if answer_only else active_tools(model),
                                                think=s["think"] or (level != "low" and (bool(cloud.parse(model)) or s.get("reasoning", "auto") != "auto")), reasoning=level,   # auto never makes the local brain think: 30-45 s per step on a 6 GB GPU
                                                on_text=unless_stopped(stream.feed if stream else None),
                                                fallback=None if local_route else s["model"], on_think=unless_stopped(reasoning.feed),
                                                keep_alive=router.BORROW_KEEP if model != s["model"] and not cloud.parse(model) else None)
        except llm.OllamaError as e:
            if not local_route:
                raise
            reasoning.flush()
            task["model"], task["route"] = s["model"], local_route
            turns -= 1                                      # this turn produced nothing; it doesn't use up the budget
            if "Used the local brain" not in activity:
                activity.append("Used the local brain")
            task_log(f"CLOUD BRAIN NOT USED ({model}): {e} -> local route '{local_route}' now")
            think("☁️ The cloud brain didn't answer, so I'm handing this to my local "
                  + ("coding brain." if local_route == "code" else "deep-thinking brain to plan it."))
            continue
        reasoning.flush()
        used = s["model"] if llm.LAST["fallback"] else model
        task_log(f"BRAIN {time.time() - t0:.1f}s ({'local fallback' if llm.LAST['fallback'] else model}, {level})")
        think(f"⏱ Thought for {time.time() - t0:.1f}s ({used.replace('cloud:', '')})"
              + (f" → {len(calls)} step{'s' if len(calls) > 1 else ''} to do" if calls else " → answering"),
              cloud_too=False)
        if llm.LAST["fallback"] and "Used the local brain" not in activity:
            activity.append("Used the local brain")   # the cloud one failed; she carried on with the local model
            task_log(f"CLOUD BRAIN NOT USED ({model}): {llm.LAST['fallback']} -> local {s['model']}")
            think("☁️ The cloud brain didn't answer, so I used my local one.")
            if task and cloud.parse(task_brain(s, task)):   # finish this task locally, with the local plan/coder help
                task["model"] = s["model"]
                choose_local_route(task, s)
                if task.get("route"):
                    task_log(f"CLOUD FELL BACK -> local route '{task['route']}' from next turn")
        if stream:
            stream.flush()
        state["messages"].append(stored)
        if text:
            task_log(f"NOVA SAYS: {text[:300]}")
        did_something = did_something or bool(calls)
        claimed_body = bool(BODY_CLAIM_RE.search(text or "")) and not state.get("react_after")
        invent = state.get("invent")        # "make one up" - but no new_move call yet (react_after is set by new_move)
        if invent and not calls and not nudged and not state.get("react_after"):
            nudged = True
            think(f"🙈 I said I'd make the '{invent}' move but didn't - doing it properly now.")
            state["messages"].append({"role": "user", "content":
                f"[note from Nova's system] You haven't made the move yet. Call new_move NOW for '{invent}': a short "
                "name, a duration in seconds, and 4-8 keys (each {\"at\": 0..1, plus poses such as right_arm_out, "
                "left_arm_out, lean_side, jump, head_nod}). Then say one short line."})
            continue
        if not calls and not nudged and (claimed_body or (not did_something and PROMISE_RE.search(text or ""))):
            # Small models sometimes *say* "I'm clicking it now" without calling a tool. Make her follow through.
            nudged = True
            think("🙈 I said I'd do it but didn't actually do anything - doing it properly now.")
            state["messages"].append({"role": "user", "content":
                "[note from Nova's system] You said you'd do something but didn't call a tool, so nothing happened. "
                "Call the right tool now. If no tool can do it, say so honestly in one sentence."})
            continue
        rec = task.get("recipe") if task else None
        if not calls and rec and task.get("calls", 0) <= rec["calls"]:
            # She was handed a skill's steps and stopped without calling a single tool: nothing happened.
            if not rec["nudged"]:
                rec["nudged"] = True
                think(f"🙈 I haven't done any step of my '{rec['name']}' skill yet - doing it now.")
                state["messages"].append({"role": "user", "content":
                    f"[note from Nova's system] You haven't done any step of the skill '{rec['name']}' yet - nothing has "
                    "happened. Do its first step now with a tool."})
                continue
            task_log(f"HALLUCINATED DONE: skill '{rec['name']}' handed over, no tool called after it")
            text = f"I didn't actually run my '{rec['name']}' skill - nothing happened. Want me to try again?"
        elif (not calls and not did_something and task and not task.get("calls") and not task["steps"]
              and DONE_CLAIM_RE.search(text or "") and task.get("skill")):
            # "You're all set!" with no tool called anywhere in this request: it can't be true.
            if not claim_nudged:
                claim_nudged = True
                think("🙈 I said it was done but I haven't done anything - doing it properly now.")
                state["messages"].append({"role": "user", "content":
                    "[note from Nova's system] You said it is done, but you have not called any tool, so nothing "
                    "happened. If the request needs an action, do it now with the right tool (or run_skill for a "
                    "skill). If you can't, say honestly that you didn't do it."})
                continue
            task_log(f"HALLUCINATED DONE: '{(text or '')[:120]}' with no tool called")
            text = "I haven't actually done that yet - tell me again and I'll do it properly."
        if (not calls and task and task["steps"] >= LOOK_FROM_STEP and _big_brain(model)
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
            think("🔁 My check says part of it isn't done yet - going back to finish it.")
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
            if calls:
                lead_ins.add(len(spoken) - 1)
            if not (stream and llm.supports_tools(model)):
                unsaid.append(text)
        if not calls:
            # "Sure! Let's run the command:" said before a step isn't part of the answer once a real answer follows
            # (it read "...in your terminal: Ah! Looks like Python 3.12..."). Out loud she still says both.
            answer_only_text = [t for i, t in enumerate(spoken) if i not in lead_ins]
            return result(" ".join(answer_only_text if text else spoken).strip() or "Done!")
        sig = json.dumps([(c["name"], c["args"]) for c in calls], sort_keys=True, default=str)
        repeatable = any(c["name"] in REPEATABLE for c in calls)
        if sig == last_calls and same_calls >= REPEAT_LIMIT and not repeatable:
            # Told to stop repeating and did it again: end here rather than spending the whole step budget on it.
            state["messages"].extend(llm.tool_result_message(model, c, "Not run - same as before.") for c in calls)
            task_log(f"STUCK: {calls[0]['name']} called {same_calls} times in a row with the same arguments")
            think("🔁 I keep doing the same step and getting nowhere - stopping here.")
            line = "I kept repeating the same step without getting anywhere, so I stopped. Can you tell me a bit more?"
            unsaid.append(line)
            return result((" ".join(spoken) + " " + line).strip())
        if any(is_risky(c) for c in calls):
            state["pending"] = calls
            think("✋ I need your OK before I " + ", ".join((ask_text(c) if c["name"] == "run_skill" else ASK.get(c["name"], c["name"])).lower()
                                                            for c in calls if is_risky(c)) + ".")
            return result(" ".join(spoken).strip(),
                          pending=[{"tool": c["name"], "label": ask_text(c), "input": c["args"]}
                                   for c in calls if is_risky(c)])
        if task is not None:
            task["calls"] = task.get("calls", 0) + len(calls)
        state["messages"].extend(execute(calls, True, activity, model))
        pause = state.pop("skill_pause", None)
        if pause:                           # a skill asks the user something (confirm / ask steps)
            asked = ask_skill_question(pause)
            unsaid.append(asked["reply"])
            return result(asked["reply"], pending=asked["pending"])
        final = state.pop("skill_final", None)
        if final and all(c["name"] == "run_skill" for c in calls):
            state["messages"].append({"role": "assistant", "content": final})
            unsaid.append(final)
            return result(final)
        # Compare only what the tool itself said: the "[check after step N]" note added to it changes every step.
        outs = json.dumps([str(o).split("\n[check after step")[0] for o in state.get("last_outs") or []])
        same = sig == last_calls and outs == last_outs and not repeatable
        same_calls, last_calls, last_outs = (same_calls + 1 if same else 1), sig, outs
        if same_calls == REPEAT_LIMIT and not repeatable:
            think("🔁 I've done that same step a few times now - time to use what I've got.")
            state["messages"].append({"role": "user", "content":
                f"[note from Nova's system] You have now called {calls[0]['name']} {same_calls} times in a row with "
                "the same arguments, and it won't give anything new. Don't call it again. Answer the user now with "
                "what you already know, or do a different step if one is really needed."})
    if task is not None:
        task["paused"] = True               # "keep going" now carries on with this task
    think("🛑 That's a lot of steps - pausing here.")
    line = "That was a lot of steps, so I paused here. Say “keep going” and I'll carry on."
    unsaid.append(line)
    return result((" ".join(spoken) + f" ({line})").strip())


def ready_info():
    s = settings()
    models = llm.list_models()
    model = s["model"]
    active = brain(s)
    info = {"avatar_roam": body_module.roam_mode(s), "routing": {k: v for k, v in router.options(s).items() if k in router.ROUTING_KEYS},
            "provider": s.get("provider", "ollama"), "cloud_models": s.get("cloud_models") or {},
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
                   autonomy=s["autonomy"], think=s["think"], reasoning=s.get("reasoning", "auto"), camera=s["camera"], screen=s["screen"],
                   notifications=s["notifications"], notif_sleep=s.get("notif_sleep", False), deep_sleep=s.get("deep_sleep", True), listen_pause=s.get("listen_pause", 0.7), listen_max=s.get("listen_max", 10), phone_calls=s.get("phone_calls", True), email_cloud=bool(s.get("email_cloud")),
                   phone_quiet=s.get("phone_quiet", True), phone_notes_cloud=s.get("phone_notes_cloud", False), suggest=s.get("suggest", False), asleep=nap["asleep"], deep_asleep=nap.get("deep", False),
                   reaction_sounds=s.get("reaction_sounds", True), power_ok=s.get("power_ok", False),
                   presence=s.get("presence", False), presence_lock=s.get("presence_lock", False),
                   error_help=s.get("error_help", True), barge_in=s.get("barge_in", True), away=presence.state["away"],
                   presence_note=presence.state["note"], presence_checks=presence.state["checks"],
                   autostart=STARTUP_LNK.exists(), character=characters.get(s.get("character"))["id"],
                   characters=characters.choices(),
                   voice=s["voice"], wake=s["wake"], wake_phrase=wake_phrase(), voice_name=mouth.voice_name, ears=ears.status, heard=ears.heard,
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
    if body.get("avatar_roam") in body_module.ROAM_MODES:
        s["avatar_roam"] = body["avatar_roam"]
    if body.get("character") in characters.CHARACTERS and body["character"] != s.get("character"):
        s["character"] = body["character"]
        apply_character(s["character"])
    if "think" in body:
        s["think"] = bool(body["think"])
    if body.get("reasoning") in REASONING_CHOICES:
        s["reasoning"] = body["reasoning"]
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
        set_ears(s["wake"])
    if body.get("autonomy") in ("careful", "trusted", "full"):
        s["autonomy"] = body["autonomy"]
    if "suggest" in body:
        s["suggest"] = bool(body["suggest"])
    if "notifications" in body:
        s["notifications"] = bool(body["notifications"])
    if "power_ok" in body:
        s["power_ok"] = bool(body["power_ok"])
    if "reaction_sounds" in body:
        s["reaction_sounds"] = bool(body["reaction_sounds"])    # avatar.py reads it through sfx.enabled()
    if isinstance(body.get("routing"), dict):       # 🔀 the Model dialog's smart-switching choices -> config/model_routing.json
        try:
            router.save(body["routing"])
        except (TypeError, ValueError):
            pass
        for k in router.ROUTING_KEYS:               # a copy in settings.json would override the file
            s.pop(k, None)
    try:
        if "listen_pause" in body:
            s["listen_pause"] = round(min(max(float(body["listen_pause"]), LISTEN_PAUSE[0]), LISTEN_PAUSE[1]), 2)
        if "listen_max" in body:
            s["listen_max"] = min(max(int(body["listen_max"]), LISTEN_MAX[0]), LISTEN_MAX[1])
    except (TypeError, ValueError):
        pass
    apply_listening(s)
    for k in ("presence", "presence_lock", "error_help", "notif_sleep", "deep_sleep", "email_cloud", "phone_calls", "phone_notes_cloud", "phone_quiet", "barge_in"):
        if k in body:
            s[k] = body[k] is True
    save_settings(s)
    refresh_barge(s)
    if "notifications" in body:
        notif_watcher.start() if s["notifications"] else notif_watcher.stop()
    if "notifications" in body or "phone_quiet" in body:
        sync_phone_banners()
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


@app.get("/api/look")
def get_look():
    """3D Nova's saved look (nova/avatar/body.py) - the 3D preview page dresses her in it."""
    from nova.avatar import body
    return jsonify(body.load_look())


@app.get("/api/model")
def get_model():
    """The chosen character's 3D model for nova3d.html (characters/<id>/: a VRM made in VRoid Studio): where to load
    it, the colours it was made with (so change_look can tint it) and how to fit the moves onto her body.
    ?character=lucy asks about another character (testing). vrm is null when she has no model (toon body)."""
    from nova.avatar import body
    ch = characters.get(request.args.get("character") or settings().get("character"))
    there = characters.model_path(ch) is not None
    return jsonify(id=ch["id"], name=ch["name"], vrm=f"/characters/{ch['id']}/model.vrm" if there else None,
                   base={**body.DEFAULT_LOOK, **ch["look"]}, fit=ch["model"]["fit"])


@app.get("/characters/<cid>/model.vrm")
def character_model(cid):
    """A character's VRM file (characters/<id>/model.vrm), only for this laptop's own pages."""
    if cid not in characters.CHARACTERS:
        return ("", 404)
    path = characters.model_path(characters.get(cid))
    if not path:
        return ("", 404)
    return send_file(path, mimetype="application/octet-stream", max_age=0)


avatar_reload_hook = None
show_window_hook = None  # set by desktop.py


@app.post("/api/wake")
def wake_by_click():
    """She was clicked while asleep (avatar.py): wake her up - ears back on, the nap clock starts again."""
    was = nap["asleep"]
    nap["last_task"] = time.time()
    wake_body(clicked=True)
    if was:
        task_log("WOKE by a click")
    return jsonify(ok=True, was_asleep=was)


@app.post("/api/show_window")
def show_window():
    wake_body()            # clicking her wakes her up
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


def use_character_voice_and_name(ch):
    """Her character's voice, and the name she answers to ("Hey Lucy" wakes Lucy, not "Hey Nova")."""
    global JUST_STOP_RE
    voice.NEURAL_VOICE = ch["voice"]
    voice.set_wake(characters.wake_names(ch), ch["wake"]["hint"], ch["name"])
    JUST_STOP_RE = just_stop_re(voice.NOVA_NAMES)


def apply_character(cid):
    """Switch to another character now: her voice at once, her 3D look (live 3D), her personality from the next
    request on. -> the character."""
    ch = characters.get(cid)
    use_character_voice_and_name(ch)
    if convo["on"]:      # an open conversation belonged to the old character: the new one waits for her own name
        convo["on"] = False
        listen_state["single"] = False
        ear_close()
    task_log(f"CHARACTER {ch['name']}: voice {ch['voice']}, wake word \"Hey {ch['name']}\"")
    with lock:                # a fresh chat: the small brain copies its old name and tone from the history otherwise
        state["messages"].clear()
        state["pending"] = None
    if mouth.voice_name and mouth.voice_name.startswith("Kokoro"):
        mouth.voice_name = f"Kokoro {ch['voice']} (natural voice)"
    try:
        look, _ = body_module.change_look({}, ch["look_preset"])
        avatar_client.send("look", look=look, timeout=3)
    except Exception as e:
        task_log(f"CHARACTER look not applied: {e}")
    return ch
events = []  # things that happened by voice, for the chat window to show


def push_event(e):
    events.append(e)
    del events[:-MAX_EVENTS]     # the chat page may be closed for hours: keep only the latest


mouth.on_start = lambda words, timing="": task_log(f"VOICE starts: {words[:60]}  ({timing})")   # "she's slow / out of sync"
mouth.on_gap = lambda secs, who, words="": task_log(f"VOICE PAUSE {secs:.1f}s mid-reply, waiting for the {who} "
                                                    f"(before: {words[:40]})")   # brain = text late, voice = synth slow


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
    r"what('?s| is) open|what do you see on|"
    r"(any |a |some )?(suggestions?|tips?|ideas?)\b[^.?!]{0,30}\b(screen|working on|doing|this)|"
    r"what should i do next|help me with (this|what i'?m doing))\b", re.I)


# Text the user highlighted: "read this selected text", "what does the highlighted part say", "padho jo select kiya".
SELECTION_ASK = re.compile(r"\b(selected|selection|highlighted|highlight|select kiya|select ki)\b", re.I)
# ...and more than reading it out: then the brain gets the text and does the rest.
READ_MORE_ASK = re.compile(r"\b(summar|translat|explain|mean|fix|rewrite|correct|improve|check|reply|answer|why|"
                           r"short|simplif|convert|count|search|google|save|note|email|type|paste|samjha|matlab)", re.I)


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
    """Run the long system prompt through the model once at start-up, so the first real question isn't the slow one.
    Also loads the small model that picks facts and tools for each request (CPU, a few seconds)."""
    retrieval.load_in_background()
    try:
        model = settings()["model"]
        llm._post("/api/chat", {"model": model, "stream": False, "keep_alive": llm.KEEP_ALIVE,
                                "messages": [{"role": "system", "content": system_prompt(model)},
                                             {"role": "user", "content": "hi"}],
                                "tools": llm._to_ollama_tools(toolpick.schemas(toolpick.pick("hi")))
                                if llm.supports_tools(model) else None,
                                "options": {"num_ctx": llm.NUM_CTX, "num_predict": 1}}, timeout=300)
    except Exception:
        pass


# ------------------------------------------------------------------ 👀 "watch me" (recorder.py) and "show me this step"
# "Watch me" and "done" are handled here in code, not by the brain: the small model must never be the one that decides
# when the mouse and keyboard are being watched.

WATCH_START_RE = re.compile(r"^\W*(ok(ay)?\W+|hey\W+)?(nova\W+)?(please\W+)?(watch me\b|watch (how|what) i do\b|"
                            r"watch and learn\b|let me show you\b|i'?ll show you( how)?\b|learn (this|from me)\b|"
                            r"record (this|what i do)\b)", re.I)
WATCH_DONE_RE = re.compile(r"^\W*(ok(ay)?\W+|so\W+)?(nova\W+)?(i'?m |i am |all )?(done|finished|that'?s it|that'?s all|"
                           r"stop watching|you can stop( watching)?)(\W+nova)?\W*$", re.I)


def watch_command(text):
    """'watch me' / 'done' -> the answer dict, or None if the request is something else."""
    t = _unspoken(text).strip()
    if recorder.recording() and WATCH_DONE_RE.search(t):
        return _watch_answer(finish_watching())
    if WATCH_START_RE.search(t):
        return _watch_answer(start_watching_now(t))
    return None


def _watch_answer(text):
    pause = state.pop("skill_pause", None)          # "change the skill like this?" after showing a step
    if pause:
        return ask_skill_question(pause, append=False)
    return {"reply": text, "activity": ["Watched you" if "Got it" in text else "Started watching"], "say": text}


def start_watching_now(goal=""):
    if state.get("remote"):
        return "I can only watch you when you're at the laptop."
    if recorder.recording():
        return "I'm already watching - say 'done' when you're finished."
    recorder.stop_listening()
    err = recorder.start(goal=goal, mode="record")
    if err:
        return err
    task_log(f"WATCHING started: {goal[:100]}")
    think("👀 Watching how you do it - say 'done' when you're finished.")
    if avatar_client.running():
        avatar_client.send("say", text="👀 Watching - say 'done' when you're finished", secs=6)
    teach = state.get("teach")
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
    task_log(f"WATCHING stopped: {len(events)} events -> {len(trace)} steps")
    teach = state.pop("teach", None)
    steps, why = skills.steps_from_trace(trace, goal)
    if teach:
        return teach_propose(teach, steps, why)
    if not steps:
        return f"I watched, but didn't catch anything I could repeat. {why}".strip()
    state["last_trace"] = {"goal": goal, "trace": trace, "from": "watching", "ts": time.time()}
    lines = "; ".join(skills.render_step(st)[:60] for st in steps[:6]) + ("; ..." if len(steps) > 6 else "")
    return (f"Got it - I saw {len(steps)} steps: {lines}." + (" (I stopped watching after 10 minutes.)" if hit else "")
            + " What should I call this skill, and what will you say to start it?")


def watch_note():
    """Right after 'watch me ... done': the next request is probably the skill's name."""
    lt = state.get("last_trace") or {}
    if lt.get("from") != "watching" or time.time() - lt.get("ts", 0) > 900:
        return ""
    return ("\n\nYou just watched the user do a task and asked what to call it. If they now give it a name (and the "
            "words they'll say to start it), call save_task_as_skill with that name and examples. Don't do the task again.")


def teach_propose(teach, steps, why):
    """The user showed one step of a skill: offer to change that step (replace a failed one, or add what they did)."""
    name, sid = teach["skill"], teach["sid"]
    steps = [st for st in (steps or []) if "report" not in st][:5]
    if not steps:
        state.pop("skill_run", None)
        return (f"I didn't catch any steps I could use" + (f" ({why})" if why else "")
                + f", so I left my '{name}' skill as it was.")
    lines = [skills.render_step(st) for st in steps]
    if teach["mode"] == "replace":
        ops = [{"op": "replace_step", "id": sid, "step": lines[0]}] + [
            {"op": "insert_step", "after": sid, "step": l} for l in reversed(lines[1:])]
        what = f"do step {sid} like this"
    else:
        ops = [{"op": "insert_step", "after": sid, "step": l} for l in reversed(lines)]
        what = f"add this after step {sid}"
    run = state.get("skill_run")
    if run is not None:
        run["taught"] = True                    # one lesson per run
        if teach["mode"] == "replace":
            run["asked"] = sid                  # the user did that step themselves
    q = f"Change my '{name}' skill to {what}: {'; '.join(l[:60] for l in lines)}?"
    state["skill_pause"] = {"name": name, "question": q, "resume_at": teach["resume_at"], "input": teach.get("input", ""),
                            "teach": {"phase": "apply", "ops": ops}}
    return f"Paused my '{name}' skill to ask the user: {q}"


def _can_teach(s):
    """Can this skill run watch for the user taking over (and learn the step from them)?"""
    return (recorder.ENABLED and not state.get("remote") and s["risk"] != "read_only"
            and settings().get("skill_teach", True))


def _takeover_pause(s, run, sid, resume_at):
    """The user clicked or typed while the skill was working: stop and offer to learn from them."""
    mode = "replace" if any(f[0] == sid for f in run["failed"]) else "insert"
    q = (f"You took over after step {sid} of my '{s['name']}' skill. Want to show me how it should go? "
         "Say yes, do it yourself, then say 'done'.")
    state["skill_run"] = run
    state["skill_pause"] = {"name": s["name"], "question": q, "resume_at": resume_at, "input": run["input"],
                            "teach": {"phase": "show", "sid": sid, "mode": mode}}
    task_log(f"SKILL TAKE-OVER {s['name']} after {sid} ({mode})")
    think(f"✋ You took over after step {sid} - I'll stop and ask.")
    return f"Paused my '{s['name']}' skill: the user took over after step {sid}."


def _say_final(final, activity):
    state.pop("skill_final", None)
    state["messages"].append({"role": "assistant", "content": final})
    return {"reply": final, "activity": activity, "say": final}


# ------------------------------------------------------------------ body commands: "dance!" is done by code
# The small local brain often *said* "I'm dancing!" without calling react, and when told "you're not dancing" it
# insisted it was (2026-11-18 log). A plain "dance / clap / spin..." is now played directly - faster, too (no brain
# round trip) - and a move she doesn't have makes her ASK instead of pretending.

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
    if state.get("remote"):
        return None
    t = _BODY_POLITE.sub("", _unspoken(text).strip()).strip()
    if not t or len(t.split()) > 9:
        return None
    asked = state.pop("body_ask", None)       # she asked "make one up, or my usual dance?" - this is the answer
    if asked and time.time() - asked["at"] < 180:
        if re.search(r"\b(make|invent|create|new one|try|learn|yes|yeah|yep|sure|ok(ay)?|go ahead)\b", t, re.I) \
                and not re.search(r"\b(usual|normal|regular|old|known|your own)\b", t, re.I):
            task = state.get("task")
            if task and task.get("tools") and "new_move" not in task["tools"]:
                task["tools"] = list(task["tools"]) + ["new_move"]
            state["messages"][-1]["content"] += (f"\n[note from Nova's system: they want you to invent the move "
                                                 f"'{asked['what']}' now with new_move, then say one short line]")
            state["invent"] = asked["what"]
            try:        # designing the poses takes the brain ~20 s: say something at once instead of going quiet
                mouth.say(random.choice(("Okay, let me think of one.", "Alright, give me a moment to make it up.")))
            except Exception:
                pass
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
            state["body_ask"] = {"what": what, "at": time.time()}
            task_log(f"BODY ASK: unknown move '{what}'")
            return {"reply": reply, "activity": [], "say": reply}
        reply = random.choice(("Sure, watch this!", "Here goes!", "Okay, watch me!"))
    if not avatar_client.running():
        reply = "I can't move right now: my 3D body isn't on the screen. Turn it back on from the tray menu."
        return {"reply": reply, "activity": [], "say": reply}
    state["react_after"] = {"name": name, "repeat": times}
    task_log(f"BODY COMMAND: {name}" + (f" x{times}" if times else ""))
    return {"reply": reply, "activity": [FRIENDLY["react"]], "say": reply}


def look_first(text, activity):
    """Small models sometimes 'answer' about the user or the screen without looking. If the user clearly asked
    Nova to look, take the picture first and hand her the result, so she describes what's really there.
    (If camera/screen viewing is switched off, the tool itself tells Nova so - no made-up answers either way.)
    Clear video-player requests (full screen, captions...) are done the same way."""
    watched = watch_command(text)       # 👀 "watch me" / "done": code decides, never the brain
    if watched:
        return watched
    moved = body_command(text)          # 💃 "dance!" - code plays it, the brain can't pretend it did
    if moved:
        activity.extend(moved["activity"])
        return moved
    text = _unspoken(text)       # only the user's words: the 📱 phone note itself mentions "the screen"
    video_action = next((a for rx, a in VIDEO_ASK if rx.search(text)), None)
    if video_action and tools._video_window():
        name, args = "video_control", {"action": video_action}
    elif SELECTION_ASK.search(text):     # before SCREEN_ASK: a screenshot only showed her one line of the selection
        name, args = "read_selection", {}
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
    think({"look_at_screen": "👀 You asked about your screen - I'll look at it first.",
           "look_through_camera": "📷 You want me to see you - I'll look through the camera first.",
           "video_control": "🎬 That's a video-player button - pressing it directly.",
           "read_selection": "📋 You selected some text - I'll copy it first."}.get(
        name, "📌 That's a plain fact - I'll check it instead of guessing."))
    model = brain()
    call = {"id": "look_0", "name": name, "args": args}
    state["messages"].append({"role": "assistant", "content": "",
                              "tool_calls": [{"function": {"name": call["name"], "arguments": call["args"]}}]}
                             if llm.supports_tools(model) else
                             {"role": "assistant", "content": json.dumps({"tool": call["name"], "args": call["args"]})})
    state["messages"].extend(execute([call], True, activity, model))
    out = state["last_outs"][0]
    if name == "read_selection" and not READ_MORE_ASK.search(_unspoken(text)) and not out.startswith(("Error", "Nothing is")):
        # Just "read it": say the text itself. Through the brain it came out as one line (a summary, or the local
        # model's short reply limit), so the words are read out word for word here instead.
        return {"reply": out, "activity": activity, "say": out.split("\n[... cut here")[0]}


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


def answer(text, quiet=False, remote=False):
    """Run one user message through Nova. Returns (result dict, http status).
    quiet: don't say the answer out loud (📱 phone requests - the user isn't at the laptop).
    remote: the request came from the phone, so every action that changes something asks first (is_risky)."""
    note_task()
    with lock:
        stop_flag.clear()           # a Stop pressed for the last request doesn't cancel this one
        state["remote"] = remote
        state.pop("sleep_after", None)
        state.pop("invent", None)
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
        resume = new_task(text)
        # (Tried also passing the time and battery here to save a tool call: the small model then read the line
        # out loud and stopped calling tools for other facts. Only the window list is passed.)
        state["messages"].append({"role": "user", "content":
                                  text + resume + (f"\n[open windows, front first: {screen}]" if screen else "")})
        try:
            out = look_first(text, activity)
            if out:     # done without the brain (reading out selected text)
                state["messages"].append({"role": "assistant", "content": out["reply"]})
                nap["last_task"] = time.time()
            else:
                out = agent_loop(activity)
        except Exception as e:
            del state["messages"][n:]  # roll back so the history stays valid
            state["pending"] = old_pending
            state.pop("task", None)
            state.pop("images", None)
            think(f"💥 Something went wrong: {e}", cloud_too=False)
            thoughts_done()
            if isinstance(e, llm.OllamaError):
                return {"error": str(e)}, 503
            task_log(f"ERROR in answer: {type(e).__name__}: {e}")
            return {"error": f"Something went wrong inside Nova ({type(e).__name__}: {e})."}, 500
        _cap_history()
    thoughts_done()
    if quiet:
        out.pop("say", None)
    else:
        speak(out.pop("say", out.get("reply")))
        bubble(out.get("reply"))
    after_turn(text, out)
    if state.pop("sleep_after", False):     # her brain understood "go to sleep" (go_to_sleep tool): now really sleep
        go_to_sleep("asked", quiet=True)
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
    state["react_after"] = {"name": name, "repeat": times}
    activity.append(FRIENDLY["react"])
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
        neural = getattr(mouth, "neural", None)
        try:
            made = sfx.save_move_sound(move["name"], snd, say=neural.synth if neural else None)
        except Exception as e:
            made = None
            task_log(f"MOVE SOUND failed: {e}")
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
    state["react_after"] = {"name": move["name"], "repeat": times}
    activity.append(FRIENDLY["new_move"])
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
            while mouth.talking() and time.time() < end:
                time.sleep(0.05)
        if not mouth.talking() or not wait_for_speech:
            out = avatar_client.send("react", name=reaction, repeat=repeat, timeout=2)
            if out and out.get("played"):
                mouth.hold(sfx.length(reaction) + 0.3)     # its sound effect: keep the mic from hearing it
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
        asked = state.pop("react_after", None)      # the brain's own pick (react tool) wins over the automatic one
        state.pop("invent", None)
        stage = out.get("stage_reaction") or shared.stage_reaction(shared.stage_directions(out.get("reply") or "")[1])
        if not asked and stage:      # she wrote '[react: "laugh"]' / '*spins*' instead of calling react: do it
            name = tools.reaction_name(stage)
            if name in REACTIONS or name in body_module.load_moves():
                task_log(f"STAGE DIRECTION without react -> playing {name}")
                asked = {"name": name, "repeat": None}
        if not asked:      # she still only *said* she's dancing (even after the nudge): make her words true
            claims = list(BODY_CLAIM_RE.finditer(out.get("reply") or ""))
            claim = bool(claims)
            word = next(((c.group("v") or c.group("w")).lower() for c in claims if c.group("v") or c.group("w")), "")
            name = {"danced": "dance", "clapped": "clap", "spun": "spin", "twirling": "spin"}.get(word) or BODY_WORDS.get(word)
            if claim and not name:     # "reacting now": the move the user asked for, if it's one she has
                name = next((BODY_WORDS[w] for w in re.findall(r"[a-z]+", clean_text.lower()) if w in BODY_WORDS), None)
            if name:
                task_log(f"BODY CLAIM without react -> playing {name}")
                asked = {"name": name, "repeat": None}
        if asked:      # asked to act something out ("no, dance properly!" is a correction AND a request)
            react_later(asked["name"], repeat=asked["repeat"])
        elif corrected:
            react_later("facepalm", wait_for_speech=False)
        else:
            react_later(pick_reaction(clean_text, out))
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
# Whisper often mishears it ("Noah got to sleep", "go too sleep"), so the verbs and the "to" are loose. Only short
# utterances count (wants_sleep), so "I can't go to sleep at night, any tips?" is a normal question.
SLEEP_RE = re.compile(r"\b((go|got|goes|going|gotta|gonna|gone)( (to|too|two|2|ta))? ?sleep|sleep now|(go )?back to sleep|(you can|time to|now) sleep|"
                      r"sleep,? (nova|noah|now)|(nova|noah),? sleep|take a nap|nap time|go to bed|stop listening|"
                      r"good ?night|that'?s all|bye,? (nova|noah)|so ?jao|sunna band|shubh ratri|bas itna hi)\b", re.I)
SLEEP_MAX_WORDS = 8


# A bare "Nova, stop" / "ruko" / "bas" / "chup" said to her is a command, not a chat: it ends whatever she's doing
# (talking, a task) and goes nowhere near her brain. Before, it went to the brain, which chatted back ("I'm here,
# Vikrant - ready to help") - the opposite of stop. "Stop the music" has more words, so it still goes to the brain.
def just_stop_re(names):
    return re.compile(rf"^\W*(?:(?:hey|hi|ok|okay|arey)\W+)?(?:{names}\W+)?(?:please\W+)?"
                      r"(?:stop(?: it| that| talking| now)?|ru+c*k*h?o|roko|ruk jao|bas(?: karo)?|chup(?: raho| ho jao)?|"
                      r"enough|quiet|be quiet|shut up|that'?s enough)"
                      rf"(?:\W+(?:{names}|please|now|yaar))*\W*$", re.I)


JUST_STOP_RE = just_stop_re(voice.NOVA_NAMES)
use_character_voice_and_name(characters.get(settings().get("character")))   # her name, voice and "stop" words


def just_stop(text):
    return bool(JUST_STOP_RE.match(_unspoken(text)))


def stopped_by_voice(text, via="silent"):
    """'Nova, stop' while she's quiet (or right after she was talked over): stop everything, say nothing."""
    was_busy = stop_now()
    task_log(f"VOICE STOP: {text[:40]!r} ({'stopped a task' if was_busy else 'nothing was running'})")
    avatar_client.send("say", text="🤐 Okay.", secs=2, timeout=2)
    push_event({"reply": "🤐 Okay, stopped." if was_busy else "🤐 Okay.", "kind": "suggestion"})
    if via != "silent":
        ear_listen_after_speech() if convo["on"] else ear_close()


def wants_sleep(text):
    """Is this the user telling her to go to sleep (not just a sentence that mentions sleep)?"""
    clean = _unspoken(text)
    return bool(SLEEP_RE.search(clean)) and len(clean.split()) <= SLEEP_MAX_WORDS
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
LISTEN_PAUSE = (0.3, 2.0)       # ⚙ Senses: seconds of quiet that mean you've finished speaking
LISTEN_MAX = (4, 30)            # ⚙ Senses: the longest one spoken request may be


def apply_listening(s=None):
    """Her ears' timing from the settings (Whisper only - the Windows recognizer decides for itself)."""
    s = s or settings()
    if whisper:
        whisper.END_PAUSE = min(max(float(s.get("listen_pause", 0.7)), LISTEN_PAUSE[0]), LISTEN_PAUSE[1])
        whisper.MAX_LEN = float(min(max(int(s.get("listen_max", 10)), LISTEN_MAX[0]), LISTEN_MAX[1]))


apply_listening()
transcriber = whisper or windows_transcriber
if whisper:
    # The words so far show in 3D Nova's bubble while the user is still speaking (the final reading replaces them).
    whisper.on_partial = lambda t: avatar_client.send("say", text=f"“{t[:150]}…”", secs=3, timeout=1)
    whisper.self_voice = mouth.busy       # never take her own voice (answers, notifications, reaction sounds) as a request
    whisper.expect_reply = lambda: expecting_reply()
listen_state = {"loop": False, "rt_broken": False, "single": False}
if whisper:
    threading.Thread(target=lambda: whisper.load(), daemon=True).start()   # warm up so the first request is quick


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
        task_log("EARS ON (👂)")
        return
    ears.stop()
    convo["on"] = False
    listen_state["single"] = False
    ear_close()
    avatar_client.send("thought", text="🙉 ears off", secs=4, timeout=2)
    task_log("EARS OFF (👂) - not listening at all")


def silent_ok():
    if listen_state["rt_broken"]:
        return False
    return transcriber is whisper or voice.online_speech_on()


ASKED_SECS = 60      # a bare "yes"/"no" counts as an answer this long after she asked something


def expecting_reply():
    """Is she waiting for a yes/no? (An Allow/No question, "want to see it?", or her last answer was a question.)
    Only then is a short, slightly unclear "No." taken as an answer - otherwise it is most likely a cough."""
    return (bool(state["pending"]) or bool(offer["job"] and time.time() < offer["until"])
            or time.time() - convo.get("asked", 0.0) < ASKED_SECS)


def _still_listening():
    return convo["on"] or bool(state["pending"]) or listen_state["single"]


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
    threading.Thread(target=beat, daemon=True).start()
    return done.set


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
                task_log(f"HEARD {text[:60]!r} ({getattr(transcriber, 'timing_note', lambda: '')()})")
                heard(text, via="silent")
            elif kind in ("silence", "unclear"):
                rejected = getattr(transcriber, "rejected", "")
                ears._note(f"ignored noise: {rejected}" if rejected else
                           "heard a sound but couldn't make out words" if kind == "unclear" else "listened, heard no speech")
                if listen_state["single"]:
                    listen_state["single"] = False
                    avatar_client.send("say", text=f"Sorry, I didn't catch that 🤔 Say “{wake_phrase()}” again.", secs=4, timeout=2)
                elif (convo["on"] and time.time() - max(convo["last_heard"], nap["last_task"]) > IDLE_NAP
                      and not lock.locked() and not mouth.busy() and not convo["thinking"]):
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
        if greet:
            name = characters.get(settings().get("character"))["name"]
            announce(f"I'm awake! 👂 Talk to me. Say “{name}, go to sleep” when you're done.", spoken="I'm awake! I'm listening.")


def on_wake(conversation=False):
    """'Hey Nova' (or 'Nova, wake up'): she wakes up and stays in the conversation until told to sleep."""
    mouth.stop()
    note_task()
    if silent_ok():
        if convo["on"] and listen_state["loop"]:
            return                        # already listening
        _start_conversation(greet=conversation)
        threading.Thread(target=silent_loop, daemon=True).start()
        return
    if ear_hooks["show"] is None:      # no desktop app (plain browser): fall back to the chat page
        if show_window_hook:
            show_window_hook()
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
    task_log(f"SLEEP ({reason})")
    nap["reason"] = reason                  # "asked" = the user's choice: presence won't wake her (on_presence)
    if (reason == "asked" or was_on) and not quiet:
        line = (f"Okay, I'll take a nap in the corner. Say “{wake_phrase()}” when you need me 😴" if reason == "asked" else
                f"It's been quiet for a while, so I'll take a nap in the corner. Say “{wake_phrase()}” when you need me 😴")
        announce(line)
        sleep_body(after_words=True)      # the goodnight first, then she lies down
    else:
        sleep_body()


def confirm_pending(approved, quiet=False, remote=None):
    """Answer the Allow / No question Nova asked (from the chat page, by voice, or 📱 from the phone)."""
    with lock:
        if not state["pending"]:
            return {"reply": "", "activity": []}, 200
        if remote is not None:
            state["remote"] = remote
        stop_flag.clear()
        nap["last_task"] = time.time()
        activity = []
        new_turn()
        think("👍 You said yes - going ahead." if approved else "👌 You said no - I'll leave it.")
        again = next((p for p in state["pending"] if (p["args"] or {}).get("resume_at") is not None), None)
        if again:                            # a skill asked its own question: carry it on (or stop it) here
            state["pending"] = None
            try:
                out = resume_skill(again["args"], approved, activity)
            except Exception as e:
                think(f"💥 Something went wrong: {e}", cloud_too=False)
                thoughts_done()
                task_log(f"ERROR in skill resume: {type(e).__name__}: {e}")
                return {"error": f"Something went wrong inside Nova ({type(e).__name__}: {e})."}, 500
            thoughts_done()
            if quiet:
                out.pop("say", None)
            else:
                speak(out.pop("say", out.get("reply")))
                bubble(out.get("reply"))
            return out, 200
        if not approved:                 # "no" to a skill that only sounded right: that sentence never starts it again
            task = state.get("task") or {}
            for p in state["pending"]:
                if p["name"] == "run_skill" and task.get("skill_how") == "fuzzy" and task.get("skill") == (p["args"] or {}).get("name"):
                    skills.add_not_for(task["skill"], task.get("goal", ""))
                    task_log(f"SKILL DENIED {task['skill']}: '{task.get('goal', '')[:80]}' will not start it again")
        state["messages"].extend(execute(state["pending"], approved, activity, brain()))
        state["pending"] = None
        n = len(state["messages"])
        try:
            out = agent_loop(activity)
        except Exception as e:
            del state["messages"][n:]  # the approved action ran; only the half-finished follow-up is dropped
            think(f"💥 Something went wrong: {e}", cloud_too=False)
            thoughts_done()
            if isinstance(e, llm.OllamaError):
                return {"error": str(e)}, 503
            task_log(f"ERROR in confirm: {type(e).__name__}: {e}")
            return {"error": f"Something went wrong inside Nova ({type(e).__name__}: {e})."}, 500
    thoughts_done()
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
    if help_first():                   # "yes" right after "Notepad shows an error - want me to help?"
        helped = take_help(text)
        if helped is not None:
            push_event(dict(helped))
            if via != "silent":
                ear_listen_after_speech() if convo["on"] else ear_close()
            return
    said = take_offer(text)            # "yes" right after "your email is ready - want to see it?"
    if said is not None:
        push_event({"reply": said})
        announce(said)
        if via != "silent":
            ear_listen_after_speech() if convo["on"] else ear_close()
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
    if convo["on"] or state["pending"]:
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
        threading.Thread(target=silent_loop, daemon=True).start()


# With Whisper installed, it does all the listening (wake word and request). Otherwise Windows' recognizer does.
if whisper:
    ears = voice.WhisperEars(mouth, on_request, whisper)
    ears.paused = lambda: listen_state["loop"] or convo["thinking"]
else:
    ears = voice.Ears(mouth, on_voice)
ears.on_wake = on_wake
ears.log = task_log
ears.on_name = lambda: wake_body()      # (defined below)
ears.on_sleep = lambda: go_to_sleep("asked")
ears.is_stop = lambda text: just_stop(text)            # "Hey Nova, stop": stop, don't open a conversation
ears.on_stop = lambda text: stopped_by_voice(text)


# ------------------------------------------------------------------ barge-in: talk over her and she stops
# ("Nova, stop", "wait", "ruko"). voice.BargeIn tells the user's voice from her own echo. It only runs while her ears
# are on (👂 Hey Nova), her voice is on and ⚙ "✋ Let me interrupt her by voice" is ticked: it opens the microphone
# only while she is talking. The setting is cached here, since BargeIn asks ~30 times a second.
barge_flags = {}


def refresh_barge(s=None):
    s = s or settings()
    barge_flags.update(on=bool(s.get("barge_in", True)) and bool(s.get("voice", True)) and bool(s.get("wake")))


def barge_enabled():
    return (barge_flags.get("on", False) and not listen_state.get("ears_off") and not nap.get("deep")
            and not (whisper and whisper.muted))


def on_barge(kind, text):
    """The user talked over her. kind "stop" (stop / ruko / bas...) also stops a task she is working on; "name"
    (her name, "wait", "hold on") and "talk" (a sentence of their own) only stop her talking. Then she listens
    for what comes next."""
    if kind == "stop" and lock.locked():
        stop_now()
    mouth.stop(quick=True)
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
    barge.log = task_log
    barge.start()


# ------------------------------------------------------------------ nap time: 5 minutes without a task -> she sleeps
# Any request (typed or spoken), a wake word, or clicking her wakes her. While she sleeps she lies in the bottom-right
# corner of the screen and only "Hey Nova" (or a typed message) gets her attention.

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
        if settings()["voice"]:
            while mouth.talking() and time.time() < give_up:
                time.sleep(0.1)
        else:
            time.sleep(3)                 # (her bubble shows the line instead)
        if nap["asleep"] and nap.get("gen") == gen:     # not woken again in the meantime
            avatar_client.send("sleep", on=True, timeout=3)
    threading.Thread(target=later, daemon=True).start()


def deep_sleep_due(now=None):
    now = now or time.time()
    return (nap["asleep"] and not nap.get("deep") and settings().get("deep_sleep", True)
            and now - nap.get("since", now) >= DEEP_SLEEP_AFTER)


def go_deep():
    """Asleep for a while: turn her ears off completely (no mic, no Whisper) - the TV can talk all it likes.
    A click on her, a typed message or a phone message wakes her (wake_body turns the ears back on)."""
    nap["deep"] = True
    ears.stop()
    task_log("DEEP SLEEP - mic off until clicked or messaged")
    push_event({"reply": "💤 I'm deep asleep now - my mic is off. Click me (or type to me) to wake me up.", "kind": "suggestion"})
    avatar_client.send("sleep", on=True, deep=True, timeout=3)


def wake_body(clicked=False):
    if nap.get("deep"):
        nap["deep"] = False
        if settings()["wake"]:
            ears.start()                     # her ears back on
            task_log("WOKE from deep sleep - mic on again")
    if nap["asleep"]:
        nap["asleep"] = False
        nap["reason"] = None
        nap["gen"] = nap.get("gen", 0) + 1
        avatar_client.send("sleep", on=False, clicked=clicked, timeout=3)


def note_task():
    """The user gave her something to do (or woke her): the 5-minute nap clock starts again. It is also reset after
    every step of a task and when she finishes answering, so she naps only after 5 quiet minutes."""
    nap["last_task"] = time.time()
    wake_body()


def nap_due(now=None):
    now = now or time.time()
    return (not nap["asleep"] and now - nap["last_task"] >= IDLE_NAP and not lock.locked() and not mouth.busy()
            and not convo["thinking"] and not coworker.running())


def idle_watch():
    while True:
        time.sleep(10)
        try:
            if nap_due():
                go_to_sleep("idle")
            elif deep_sleep_due():
                go_deep()
        except Exception as e:
            task_log(f"idle watch: {type(e).__name__}: {e}")


# ------------------------------------------------------------------ co-worker jobs (coworker.py): done -> "want to see it?"

offer = {"job": None, "until": 0.0}      # the "want to see it?" she asked last - a bare "yes" answers this one
offers_waiting = []                      # earlier finished jobs whose offer wasn't answered yet: (job, until)
SHOW_RE = re.compile(r"^\W*(yes|yeah|yep|sure|ok(ay)?|haan?|show( it| me)?|open it|let me see|please)\b", re.I)
OFFER_SECS = 900


def write_text(prompt, max_tokens=700):
    """Her brain as a writer (emails, documents) - no tools, a longer answer than a spoken reply."""
    s = settings()
    msgs = [{"role": "system", "content": "You are a skilled writer helping the user. Follow the requested format exactly."},
            {"role": "user", "content": prompt}]
    _, text, _ = llm.chat(brain(s), msgs, [], fallback=s["model"], num_predict=max_tokens)
    return text


def job_done(job):
    nap["last_task"] = time.time()
    if job["status"] == "stopped":                  # the user stopped it - stop_background_job already said so
        return
    if job["status"] != "done":
        line = f"Sorry, the {job['kind'].replace('_', ' ')} didn't work: {job['error']}"
        push_event({"reply": line, "kind": "job"})
        announce(line)
        tell_away(line)
        return
    wake_body()
    if offer["job"] and offer["job"] is not job and time.time() < offer["until"]:
        offers_waiting.append((offer["job"], offer["until"]))     # two jobs done close together: keep both
    offer.update(job=job, until=time.time() + OFFER_SECS)
    line = job["result"] + " Want to see it?"
    push_event({"reply": "✅ " + line, "kind": "job", "offer": {"id": job["id"], "label": "Show it"}})
    announce("✅ " + line, spoken=line)
    tell_away("✅ " + job["result"] + " It's on the laptop for when you're back.")
    react_later("happy")
    if settings()["wake"] and not convo["on"] and silent_ok():     # listen for the "yes"
        listen_state["single"] = True
        threading.Thread(target=silent_loop, daemon=True).start()


def _next_offer():
    """After one offer is answered, ask about the next finished job still waiting. Returns the question, or ""."""
    now = time.time()
    offers_waiting[:] = [(j, u) for j, u in offers_waiting if u > now and j is not offer["job"]]
    if offer["job"] and offer["until"] > now:
        return ""
    if not offers_waiting:
        offer.update(job=None, until=0.0)
        return ""
    job, until = offers_waiting.pop()
    offer.update(job=job, until=until)
    return f" Your {job['kind'].replace('_', ' ')} '{job['title']}' is ready too - want to see it?"


def take_offer(text, force=None, job_id=None):
    """'yes' / 'no' right after "want to see it?". Returns her answer, or None if this isn't about the offer.
    job_id: the chat page's button names its own job (several can be waiting)."""
    now = time.time()
    if job_id is not None:
        live = ([(offer["job"], offer["until"])] if offer["job"] else []) + offers_waiting
        job = next((j for j, u in live if j.get("id") == job_id and u > now), None)
    else:
        job = offer["job"] if offer["until"] > now else None
    if not job:
        return None
    clean = _unspoken(text).strip()
    short = len(clean.split()) <= 8
    yes = force is True or (force is None and short and SHOW_RE.search(clean))
    no = force is False or (force is None and short and NO_RE.search(clean) and not yes)
    if not (yes or no):
        return None
    if offer["job"] is job:
        offer.update(job=None, until=0.0)
    offers_waiting[:] = [(j, u) for j, u in offers_waiting if j is not job]
    note_task()
    if yes:
        think(f"🪟 Bringing '{job['title']}' to the front.")
        return coworker.show(job) + _next_offer()
    return "Okay! It's waiting for you whenever you want it." + _next_offer()


coworker.configure(write=write_text, on_done=job_done, on_note=lambda t: think(t, cloud_too=False))


# ------------------------------------------------------------------ notifications (notifications.py): she reads new ones out

SECRETY = re.compile(r"\b(otp|one[- ]time|code|password|passcode|pin|verification|cvv)\b", re.I)


def _hide_codes(line):
    if SECRETY.search(line):
        line = re.sub(r"\b[\dA-Z]{4,8}\b(?=[^\w]|$)", "(hidden)", line)
    return line


def _speakable(n):
    """A notification as she says it: one-time codes and passwords are never read out."""
    return _hide_codes(notifications.describe(n, 200))


# The iPhone's notifications arrive through Phone Link (notifications.py). Her BRAIN decides what each one is (a ringing
# call, a missed call, a message) and how to say it - "Rahul is calling you" / "a call from an unknown number" - so
# there are no names or phrases in code. Only the local brain sees them unless ⚙ allows the cloud one.
PHONE_NOTE_SECS = 15       # how long the brain gets before the plain text is used (a cold 4B model took >8 s live;
                           # a call rings ~25 s)
PHONE_NOTE_MAX = 4         # at most this many per burst go to the brain; the rest are read as they are
PHONE_CALL_WAIT = 2        # a ringing call waits at most this long for her own voice to finish
phone_seen = {}            # notification id -> when: the same toast is not announced twice


def sync_phone_banners():
    """⚙ "Hide Phone Link pop-ups": while she reads notifications, Phone Link's own banners are switched off so only
    she announces them. Turned back on when either switch goes off - but only if she was the one who hid them."""
    s = settings()
    hide = bool(s["notifications"] and s.get("phone_quiet", True))
    if not hide and not s.get("phone_banners_hidden"):
        return
    try:
        n = notifications.phone_banners(show=not hide)
    except Exception as e:
        task_log(f"PHONE BANNERS: couldn't change ({type(e).__name__}: {e})")
        return
    if n:
        task_log(f"PHONE BANNERS: {'hidden' if hide else 'shown again'} for {n} Phone Link source(s)")
    if bool(s.get("phone_banners_hidden")) != hide:
        s["phone_banners_hidden"] = hide
        save_settings(s)


def say_only(line):
    """Out loud only - no chat line and no bubble (iPhone notifications)."""
    if settings()["voice"] and line:
        mouth.say(line, show=False)


def ring_cue():
    """An instant ding when something from the iPhone arrives - the brain and the voice need a few seconds."""
    try:
        if sfx.play("happy"):
            mouth.hold(sfx.length("happy") + 0.3)         # keep the mic from hearing it
    except Exception:
        pass


def phone_note(n):
    """(kind, sentence) for one iPhone notification: "call", "missed_call", "message" or "other"."""
    s = settings()
    fallback = _speakable(n)
    model = brain(s) if s.get("phone_notes_cloud") else s["model"]
    msgs = [{"role": "system", "content": notifications.PHONE_PROMPT},
            {"role": "user", "content": notifications.phone_prompt(n)}]
    box = {}

    def work():
        try:
            box["text"] = llm.chat(model, msgs, [], fallback=s["model"], num_predict=60)[1]
        except Exception as e:
            task_log(f"PHONE NOTE: brain failed ({type(e).__name__}: {e})")

    t0 = time.time()
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(PHONE_NOTE_SECS)
    task_log(f"PHONE NOTE: arrived {max(0, t0 - (n.get('time') or t0)):.1f}s before the brain started, brain took {time.time() - t0:.1f}s")
    if th.is_alive():
        task_log(f"PHONE NOTE: brain took longer than {PHONE_NOTE_SECS} s - reading the plain text")
    kind, say = notifications.parse_phone_note(box.get("text"), fallback)
    return kind, _hide_codes(say)


def on_notifications(items):
    s = settings()
    if not s["notifications"] or not items:
        return
    items = [n for n in items if not notifications.is_muted(n, s.get("notif_muted"))]   # "don't read Claude's"
    if not items:
        return
    now = time.time()
    if any(notifications.is_phone(n) and n.get("id") not in phone_seen for n in items) and s.get("phone_calls", True) and (
            not nap["asleep"] or s.get("notif_sleep", False) or nap.get("reason") != "asked"):
        ring_cue()          # the moment it arrives: a sound now, her words follow once the brain and the voice are ready
    if any(notifications.is_phone(n) for n in items) and s.get("phone_quiet", True) and s.get("phone_banners_hidden"):
        threading.Thread(target=notifications.phone_banners, args=(False,),   # a new iPhone app gets a new Windows key
                         daemon=True).start()
    for k in [k for k, t in phone_seen.items() if now - t > 60]:
        phone_seen.pop(k, None)
    lines, calls, phone_lines, missed, brained = [], [], [], [], 0
    for n in items:
        if notifications.is_phone(n):
            if n.get("id") in phone_seen:
                continue
            phone_seen[n.get("id")] = now
            if brained < PHONE_NOTE_MAX:
                kind, say = phone_note(n)
                brained += 1
            else:
                kind, say = "other", _speakable(n)
            if kind in ("call", "missed_call") and not s.get("phone_calls", True):
                continue                                  # ⚙ "tell me about phone calls" is off
            task_log(f"NOTIFICATION from {n['app']} ({kind})")
            (calls if kind == "call" else phone_lines).append(say)
            if kind == "missed_call":
                missed.append(say)
        else:
            task_log(f"NOTIFICATION from {n['app']}")
            lines.append(_speakable(n))
    for line in lines:      # iPhone ones are voice only: not in the chat or her bubble (Phone Link's pop-up is hidden too)
        push_event({"reply": "🔔 " + line, "kind": "notification"})
    quiet = nap["asleep"] and not settings().get("notif_sleep", False)
    if quiet and not (calls and nap.get("reason") != "asked"):
        return              # napping: they're in the chat, but she only reads them out if ⚙ "…while I'm asleep" is on
    if calls:               # a ringing phone can't wait: not queued behind long answers, and not sent to the phone itself
        end = time.time() + PHONE_CALL_WAIT          # (it is said even in an idle nap - only "go to sleep" silences it)
        while (lock.locked() or mouth.busy()) and time.time() < end:
            time.sleep(0.2)
        for line in calls:
            say_only(line)
    if quiet:
        return              # the ringing call was the exception; everything else waits for her to wake
    for line in missed:
        tell_away(line)     # a missed call is worth knowing about when they are away from the laptop
    if phone_lines:
        end = time.time() + 30
        while (lock.locked() or mouth.busy()) and time.time() < end:
            time.sleep(0.5)
        for line in phone_lines:
            say_only(line)
    if not lines:
        return
    if len(lines) == 1:
        line = "🔔 " + lines[0]
    else:
        line = f"🔔 {len(lines)} new notifications. " + " … ".join(lines[:3])
        if len(lines) > 3:
            line += f" … and {len(lines) - 3} more."
    end = time.time() + 30
    while (lock.locked() or mouth.busy()) and time.time() < end:    # don't talk over her own answer
        time.sleep(0.5)
    announce(line, spoken=line.replace("🔔 ", "", 1))


notif_watcher = notifications.Watcher(on_notifications, every=1, on_note=lambda s: task_log(s))   # a ringing call can't wait 4 s


# ------------------------------------------------------------------ 📱 phone: talk to Nova through Telegram (phone.py)
# Answers go back as text (she doesn't speak them on the empty laptop), and every action that changes something asks
# first with Yes / No buttons, whatever the autonomy setting.

PHONE_TAG = ("[from the user's phone via Telegram; they are away from the laptop and read your answer as a text "
             "message, so keep it short and don't describe things only visible on the screen]")
AWAY_IDLE = 300          # no keyboard / mouse for 5 minutes = the user is probably away (presence.py knows better)


def user_away():
    if presence.state["away"]:
        return True
    try:
        return user_idle_secs() >= AWAY_IDLE
    except Exception:
        return False


def tell_away(line):
    """Something she just said out loud that the user would want to know even if they've left the laptop: sent to
    their phone (if paired and forwarding is on), and kept for the 'welcome back' summary."""
    if not user_away():
        return
    presence.missed(line)
    try:
        if phone.notify("🔔 " + line):
            task_log(f"PHONE forwarded: {line[:120]}")
    except Exception as e:
        task_log(f"PHONE forward failed: {type(e).__name__}: {e}")


def phone_message(text):
    task_log(f"PHONE in: {text[:200]}")
    if state["pending"] and len(text.split()) <= 4 and (YES_RE.search(text) or NO_RE.search(text)):
        out, _ = confirm_pending(bool(YES_RE.search(text)) and not NO_RE.search(text), quiet=True, remote=True)
    else:
        out, _ = answer(f"{PHONE_TAG} {text}", quiet=True, remote=True)
    push_event({"me": "📱 " + text, **{k: v for k, v in out.items() if k != "images"}})
    task_log(f"PHONE out: {(out.get('reply') or out.get('error') or '')[:200]}")
    return out


def phone_confirm(approved):
    out, _ = confirm_pending(approved, quiet=True, remote=True)
    if not (out.get("reply") or out.get("pending") or out.get("activity") or out.get("error")):
        out["reply"] = "That question isn't waiting any more."
    push_event({"me": "📱 " + ("Yes" if approved else "No"), **{k: v for k, v in out.items() if k != "images"}})
    return out


def phone_status():
    s = tools.stats()
    bat = "no battery" if s["battery"] is None else f"battery {s['battery']}% ({'charging' if s['plugged'] else 'on battery'})"
    jobs = [j for j in coworker.jobs if j.get("status") == "running"]
    return (f"💻 Laptop: CPU {s['cpu']:.0f}%, memory {s['ram']:.0f}%, disk {s['disk']:.0f}%, {bat}, on for {s['uptime_h']} h."
            + (f"\n🛠 Working on: {', '.join(j['title'] for j in jobs)}" if jobs else "")
            + ("\n😴 I'm napping." if nap["asleep"] else "")
            + ("\n🔒 Screen is locked." if presence.screen_locked() else ""))


phone = phone_link.Phone(on_message=phone_message, on_confirm=phone_confirm, on_stop=lambda: stop_now(),
                         status_text=phone_status)

# ------------------------------------------------------------------ 📱 Nova on the iPhone (mobile.py): her web app over Tailscale
# The page is installed from Safari ("Add to Home Screen"); every request is paired and answered like the Telegram
# remote: short text answers, and anything that changes something asks first with Yes / No, whatever the autonomy.

MOBILE_TAG = ("[from the user's phone app (the Nova web app on their iPhone); they may be away from the laptop and read "
              "your answer as a short text message, so keep it brief and don't describe things only visible on the screen]")


def mobile_message(text):
    task_log(f"MOBILE in: {text[:200]}")
    if state["pending"] and len(text.split()) <= 4 and (YES_RE.search(text) or NO_RE.search(text)):
        out, _ = confirm_pending(bool(YES_RE.search(text)) and not NO_RE.search(text), quiet=True, remote=True)
    else:
        out, _ = answer(f"{MOBILE_TAG} {text}", quiet=True, remote=True)
    push_event({"me": "📱 " + text, **{k: v for k, v in out.items() if k != "images"}})
    task_log(f"MOBILE out: {(out.get('reply') or out.get('error') or '')[:200]}")
    return out


def mobile_confirm(approved):
    out, _ = confirm_pending(approved, quiet=True, remote=True)
    if not (out.get("reply") or out.get("pending") or out.get("activity") or out.get("error")):
        out["reply"] = "That question isn't waiting any more."
    push_event({"me": "📱 " + ("Yes" if approved else "No"), **{k: v for k, v in out.items() if k != "images"}})
    return out


mobile = mobile_mod.Mobile(on_message=mobile_message, on_confirm=mobile_confirm, on_stop=lambda: stop_now(),
                           status_text=phone_status, state=lambda: {"asleep": bool(nap["asleep"]), "busy": lock.locked()})


def tailscale_url():
    """Nova's private https address if Tailscale is installed and 'serve' is set up: shown in settings so it can be typed
    on the phone. "" when Tailscale isn't there."""
    import shutil
    import subprocess
    exe = shutil.which("tailscale") or "C:/Program Files/Tailscale/tailscale.exe"
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=6,
                             creationflags=0x08000000).stdout
        name = (json.loads(out).get("Self") or {}).get("DNSName", "").rstrip(".")
        return f"https://{name}" if name else ""
    except Exception:
        return ""


def mobile_info():
    return {"on": bool(settings().get("mobile")), "running": mobile.running(), "code": mobile.pairing(),
            "devices": mobile.devices(), "url": tailscale_url(), "port": mobile_mod.PORT,
            "serve": f"tailscale serve --bg --https=443 http://127.0.0.1:{mobile_mod.PORT}"}


@app.get("/api/mobile")
def mobile_get():
    return jsonify(mobile_info())


@app.post("/api/mobile")
def mobile_set():
    """⚙ Nova on iPhone: switch on/off, new pairing code, remove a paired device."""
    body = _body()
    if "on" in body:
        s = settings()
        s["mobile"] = body["on"] is True
        save_settings(s)
        mobile.start() if s["mobile"] else mobile.stop()
    if body.get("new_code") is True:
        if not mobile.running():
            return jsonify(error="Switch Nova on iPhone on first.", **mobile_info()), 400
        mobile.new_pair_code()
    if "remove" in body:
        mobile.remove(str(body["remove"]))
    return jsonify(mobile_info())


# ------------------------------------------------------------------ 📧 Gmail, read-only (gmail.py)

def email_allowed():
    """Her mail is read by the local brain only - unless ⚙ allows the chosen cloud brain to see it too."""
    s = settings()
    return bool(s.get("email_cloud")) or not cloud.parse(task_brain(s))


tools.email_ok = email_allowed


@app.get("/api/gmail")
def gmail_get():
    return jsonify(**gmail.status(), connecting=gmail.connecting["on"], error=gmail.connecting["error"])


@app.post("/api/gmail")
def gmail_set():
    """⚙ Gmail: save the Google client, connect (Google's own sign-in opens in the browser), disconnect."""
    body = _body()
    try:
        if "client_id" in body or "client_secret" in body:
            gmail.save_client(_text(body, "client_id"), _text(body, "client_secret"))
        if body.get("connect") is True and not gmail.connecting["on"]:
            gmail.connecting.update(on=True, error="")

            def go():
                try:
                    gmail.connect()
                except gmail.GmailError as e:
                    gmail.connecting["error"] = str(e)
                    task_log(f"GMAIL connect: {e}")
            threading.Thread(target=go, daemon=True).start()
        if body.get("disconnect") is True:
            gmail.disconnect()
    except gmail.GmailError as e:
        return jsonify(error=str(e), **gmail.status()), 400
    return jsonify(**gmail.status(), connecting=gmail.connecting["on"], error=gmail.connecting["error"])


@app.get("/api/phone")
def phone_get():
    return jsonify(phone.info())


@app.post("/api/phone")
def phone_set():
    """⚙ Phone: save / forget the bot token, unpair, new pairing code, forward reminders on/off."""
    body = _body()
    try:
        if "token" in body:
            return jsonify(phone.set_token(_text(body, "token")))
        if body.get("unpair") is True:
            return jsonify(phone.unpair())
        if body.get("new_code") is True:
            phone.new_pair_code()
        if "forward" in body:
            phone.set_forward(body["forward"] is True)
    except phone_link.PhoneError as e:
        return jsonify(error=str(e), **phone.info()), 400
    return jsonify(phone.info())


# ------------------------------------------------------------------ 👋 presence (presence.py): back -> greet, gone -> lock

GREET_EVERY = 600        # at most one "welcome back" per 10 minutes
_greeted = {"at": 0.0, "warned": False}


def _first_name():
    try:
        p = knowledge.load()["profile"]
        return (p.get("nickname") or p.get("name") or "").split()[0][:20]
    except Exception:
        return ""


def presence_idle():
    """Seconds since the user last did anything Nova can notice: keyboard / mouse, or talking to her."""
    idle = user_idle_secs()
    # Not mouth.busy(): her OWN voice (a reminder, a notification read out to an empty room) isn't the user being
    # here - it used to count, so a notification made her "see" the user come back and greet nobody.
    if convo["on"] or convo["thinking"]:
        return 0.0
    return min(idle, max(0.0, time.time() - convo["last_heard"]))


def on_presence(ev):
    task_log(f"PRESENCE {ev} ({presence.state['note']})")
    if ev == "back" and nap["asleep"] and nap.get("reason") == "asked":
        # The user told her to sleep ("Nova, go to sleep"): coming back doesn't overrule that. Only "Hey Nova" (or a
        # typed message) wakes her, and what happened while they were away is kept for the next welcome.
        task_log("PRESENCE back, but you asked me to sleep - staying asleep and quiet")
        return
    if ev == "back" and time.time() - _greeted["at"] >= GREET_EVERY:
        _greeted["at"] = time.time()
        if nap["asleep"]:
            wake_body()
            nap.update(asleep=False, last_task=time.time())
        line = presence.welcome(_first_name())
        push_event({"reply": line, "kind": "suggestion"})
        announce(line)
        react_later("wave", wait_for_speech=False)
    elif ev == "lock_warn":
        _greeted["warned"] = True
        announce(f"Looks like you stepped away - I'll lock the laptop in {presence.LOCK_GRACE} seconds. "
                 "Move the mouse to stop me.", spoken="Looks like you stepped away. Locking the laptop in a moment.")
    elif ev == "lock":
        _greeted["warned"] = False
        tools.lock_screen()
        task_log("PRESENCE locked the screen")
    elif ev == "lock_cancel" and _greeted["warned"]:
        _greeted["warned"] = False
        bubble("Okay, not locking 👍", secs=3)


def presence_watch():
    while True:
        time.sleep(5)
        try:
            s = settings()
            if not s.get("presence"):
                if presence.state["away"] or presence.state["locking_at"]:
                    presence.reset()
                continue
            idle, before = presence_idle(), presence.state["checks"]
            evs = presence.tick(time.time(), idle, presence.screen_locked(), presence.look,
                                lock_on=bool(s.get("presence_lock")))
            if presence.state["checks"] != before:      # each camera look, for "why didn't she notice I left?"
                task_log(f"PRESENCE check {presence.state['checks']}: {presence.state['note']} "
                         f"(idle {idle:.0f}s, misses {presence.state['misses']}, away {presence.state['away']})")
            for ev in evs:
                on_presence(ev)
        except Exception as e:
            task_log(f"presence watch: {type(e).__name__}: {e}")


# ------------------------------------------------------------------ 🆘 trouble on screen (watcher.py): offer to help once

HELP_SECS = 180          # a bare "yes" answers the help offer this long
HELP_EVERY = 90          # at most one help offer per 90 s
HELP_YES_RE = re.compile(r"^\W*(help( me)?|close it|fix it|please)\b", re.I)
help_offer = {"item": None, "until": 0.0, "id": 0, "at": 0.0}
screen_watch = watcher.Watcher()
help_queue = []          # trouble found while an earlier offer was still fresh


def offer_help(item):
    help_offer.update(item=item, until=time.time() + HELP_SECS, id=help_offer["id"] + 1, at=time.time())
    line, spoken = watcher.offer_line(item)
    task_log(f"HELP OFFER {item['kind']} {item['proc']}: {item['title'][:60]} | {item['text'][:160]}")
    push_event({"reply": "🆘 " + line, "kind": "suggestion",
                "offer": {"id": help_offer["id"], "label": "Close it" if item["kind"] == "hung" else "Help me",
                          "help": True}})
    announce("🆘 " + line, spoken=spoken)
    if settings()["wake"] and not convo["on"] and silent_ok():     # listen for the "yes"
        listen_state["single"] = True
        threading.Thread(target=silent_loop, daemon=True).start()


def take_help(text, force=None, offer_id=None):
    """'yes' / 'no' right after "want me to help with it?". Returns Nova's answer dict, or None if this isn't about it."""
    item = help_offer["item"] if help_offer["until"] > time.time() else None
    if not item or (offer_id is not None and offer_id != help_offer["id"]):
        return None
    clean = _unspoken(text).strip()
    short = len(clean.split()) <= 6
    yes = force is True or (force is None and short and bool(YES_RE.search(clean) or HELP_YES_RE.search(clean)))
    no = force is False or (force is None and short and bool(NO_RE.search(clean)) and not yes)
    if not (yes or no):
        return None
    help_offer.update(item=None, until=0.0)
    note_task()
    if no:
        out = {"reply": "Okay, I'll leave it. 👍", "activity": []}
    elif item["kind"] == "hung":
        think(f"🧊 Closing {item['app']} - it stopped responding.")
        out = {"reply": watcher.close_hung(item), "activity": ["Closed a frozen app"]}
        task_log(f"HELP closed hung {item['proc']}: {out['reply']}")
    else:
        out, _ = answer(watcher.help_request(item))        # she speaks this answer herself
        return out
    announce(out["reply"])
    return out


def help_first():
    """Is the help offer newer than a finished job's "want to see it?" (so a bare yes goes to it)?"""
    return help_offer["until"] > time.time() and help_offer["at"] >= offer["until"] - OFFER_SECS


def help_step():
    """One look for trouble on screen (every 4 s)."""
    if not settings().get("error_help", True):
        return
    found = screen_watch.scan()
    if lock.locked() or state["pending"]:     # her own task opens dialogs too: she sees those herself
        return
    help_queue.extend(found)
    del help_queue[:-5]
    # Waits its turn (one offer per HELP_EVERY), and is dropped if the box closed / the app recovered meanwhile.
    # (Live test: a frozen app found 60 s after an error-box offer used to be thrown away.)
    help_queue[:] = [i for i in help_queue if watcher.still_there(i)]
    if help_queue and time.time() - help_offer["at"] >= HELP_EVERY:
        offer_help(help_queue.pop(0))


def help_watch():
    while True:
        time.sleep(4)
        try:
            help_step()
        except Exception as e:
            task_log(f"help watch: {type(e).__name__}: {e}")


@app.post("/api/help")
def help_answer():
    """The chat page's "Help me" / "Close it" / "Not now" buttons under a help offer."""
    body = _body()
    oid = body.get("id")
    out = take_help("", force=body.get("yes") is True, offer_id=oid if isinstance(oid, int) else None)
    return jsonify(out or {"reply": "That one isn't waiting any more.", "activity": []})


# ------------------------------------------------------------------ 🔌 connectors (mcp.py)

toolpick.EXTRA = mcp.tools_list
toolpick.EXTRA_GROUPS = mcp.groups


@app.get("/api/forms")
def forms_get():
    """⚙ Forms: the user's saved form details (this page only - _ours() keeps other sites out)."""
    from nova.actions import forms
    labels = {k: lbl for k, lbl, _ in forms.FIELDS}
    d = forms.load()
    return jsonify(fields=[{"key": k, "label": labels[k], "value": d[k]} for k in forms.DIALOG_ORDER])


@app.post("/api/forms")
def forms_set():
    from nova.actions import forms
    body = _body().get("details")
    if not isinstance(body, dict):
        return jsonify(error="expected details"), 400
    saved, err = forms.save(body)
    if err:
        return jsonify(error=err), 400
    task_log(f"FORMS: saved {len([v for v in saved.values() if v])} details (values not logged)")
    return forms_get()


@app.get("/api/mcp")
def mcp_get():
    try:
        text = json.dumps(mcp.load_config(), indent=2)
    except mcp.MCPError:
        text = mcp.config_file().read_text(encoding="utf-8")
    return jsonify(config=text, **mcp.summary())


@app.post("/api/mcp")
def mcp_set():
    """⚙ Connectors: save the config (the same JSON other MCP apps use) and reconnect every server."""
    raw = _text(_body(), "config")
    try:
        cfg = json.loads(raw or '{"mcpServers": {}}')
    except ValueError as e:
        return jsonify(error=f"That isn't valid JSON: {e}"), 400
    if isinstance(cfg, dict) and "mcpServers" not in cfg and cfg and all(isinstance(v, dict) for v in cfg.values()):
        cfg = {"mcpServers": cfg}          # just the servers was pasted
    try:
        mcp.save_config(cfg)
    except mcp.MCPError as e:
        return jsonify(error=str(e)), 400
    mcp.start(wait=True)
    task_log("CONNECTORS: " + "; ".join(f"{s['name']} {s['status']} {len(s['tools'])} tools {s['error'][:80]}"
                                        for s in mcp.summary()["servers"]))
    return jsonify(config=json.dumps(cfg, indent=2), **mcp.summary())


# ------------------------------------------------------------------ suggestions about what the user is working on

SUGGEST_EVERY = 600            # at most one suggestion every 10 minutes
# The whole prompt goes to the vision model as written (kind="check"). Asking first WHAT they're doing makes the small
# model's tip specific; with a bare "or answer NONE" escape it answered NONE every time.
SUGGEST_PROMPT = """This is a screenshot of the user's screen. You are their helpful co-worker sitting next to them.
Answer in exactly two lines:
WORKING ON: <what the user is doing, a few words>
SUGGESTION: <one specific, useful tip for that work: a shortcut, a faster way, a next step, or a problem you can see such as an error or typo. Talk to them directly, under 25 words. Don't describe the screen.>
Only if the screen shows nothing to work on (an empty desktop, a video or a game playing), write SUGGESTION: NONE
Never mention passwords or private details."""
suggest_state = {"last": 0.0, "title": "", "running": False}


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def user_idle_secs():
    """Seconds since the user last touched the keyboard or mouse."""
    info = _LASTINPUTINFO(ctypes.sizeof(_LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return max(0.0, (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000)


NOVA_WINDOWS = ("Nova", "Nova 3D", "Nova ear", "tk")


def _front_title():
    """The user's front-most window (Nova's own windows - her body, chat, ear - don't count)."""
    for _, title, proc in tools._windows():
        if title not in NOVA_WINDOWS and proc.lower() not in ("textinputhost.exe", "shellexperiencehost.exe"):
            return title
    return ""


def suggest_now(force=False):
    """Look at the screen and offer one suggestion. Returns it (or None). The picture stays on this laptop: only the
    local vision model sees it, never a cloud brain."""
    if suggest_state["running"] or not tools._screen_allowed():
        return None
    title = _front_title()
    if not force and (not title or
                      (title == suggest_state["title"] and time.time() - suggest_state["last"] < 1800)):
        return None
    suggest_state.update(running=True, last=time.time(), title=title)
    try:
        img = tools._grab_screen_jpeg()
        text = llm.see(img, SUGGEST_PROMPT, preferred=settings()["model"], kind="check").strip()
    finally:
        suggest_state["running"] = False
    line = re.search(r"suggestion\s*:\s*(.+)", text, re.I)
    if line:
        text = line.group(1).strip()
    elif re.search(r"working on\s*:", text, re.I):
        text = ""                                    # only the first line came back: nothing to suggest
    text = re.sub(r"^\W*(suggestion|tip)\s*:\s*", "", text, flags=re.I).strip()
    if not text or re.match(r"^\W*none\b", text, re.I) or len(text) < 8:
        task_log(f"SUGGEST: nothing for '{title[:60]}'")
        return None
    text = text[:220]
    task_log(f"SUGGEST for '{title[:60]}': {text}")
    push_event({"reply": "💡 " + text, "kind": "suggestion"})
    avatar_client.send("say", text="💡 " + text, secs=min(14, 4 + len(text) / 15), timeout=2)
    return text


def suggest_watch():
    while True:
        time.sleep(30)
        try:
            s = settings()
            if (s.get("suggest") and s["screen"] and not nap["asleep"] and not lock.locked() and not mouth.busy()
                    and time.time() - suggest_state["last"] >= SUGGEST_EVERY and user_idle_secs() < 90):
                suggest_now()
        except Exception as e:
            task_log(f"suggest watch: {type(e).__name__}: {e}")


def busy_now():
    """Is she working on something (a request, or a task in the background)? Her body shows it: at her desk."""
    return lock.locked() or convo["thinking"] or coworker.running()


def work_watch():
    """Tells 3D Nova when she starts and stops being busy, so she sits at her laptop while working."""
    last = None
    while True:
        try:
            busy = bool(busy_now())
            if busy != last and avatar_client.send("work", on=busy, timeout=2) is not None:
                last = busy
        except Exception as e:
            task_log(f"work watch: {type(e).__name__}: {e}")
        time.sleep(0.4)


_background = {"started": False}


def start_background():
    """The things that run while Nova lives in the tray: nap clock, suggestions, notifications.
    Started by the running app (desktop.py / __main__), never just by importing this module."""
    if _background["started"]:
        return
    _background["started"] = True
    threading.Thread(target=idle_watch, daemon=True).start()
    threading.Thread(target=suggest_watch, daemon=True).start()
    phone.start()                               # only if a bot token is saved
    if settings().get("mobile"):
        mobile.start()                          # the iPhone web app's server (127.0.0.1 only)
    mcp.start()                                 # 🔌 connectors in data/mcp.json, each on its own thread
    threading.Thread(target=presence_watch, daemon=True).start()
    threading.Thread(target=work_watch, daemon=True).start()
    threading.Thread(target=help_watch, daemon=True).start()
    if settings()["notifications"]:
        notif_watcher.start()
    threading.Thread(target=sync_phone_banners, daemon=True).start()
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
    if convo["on"] and time.time() - max(convo["last_heard"], nap["last_task"]) < IDLE_NAP:
        ear_listen()                      # keep listening
    elif convo["on"]:
        go_to_sleep("idle")               # 5 minutes without a task: nap
    else:
        ear_close()
    return jsonify(ok=True)


@app.post("/api/chat")
def chat():
    text = _text(_body(), "text")[:4000]
    if not text:
        return jsonify(error="empty message"), 400
    mouth.stop()  # don't talk over the user
    if wants_sleep(text):
        go_to_sleep("asked")
        return jsonify(reply=f"Okay, I'll take a nap in the corner 😴 Message me or say “{wake_phrase()}” when you need me.",
                       activity=[])
    if help_first():
        helped = take_help(text)
        if helped is not None:
            return jsonify(helped)
    said = take_offer(text)
    if said is not None:
        announce(said)
        return jsonify(reply=said, activity=[])
    out, status = answer(text)
    return jsonify(out), status


@app.post("/api/offer")
def offer_answer():
    """The chat page's "Show it" / "Not now" buttons under a finished background job."""
    body = _body()
    yes = body.get("show") is True
    job_id = body.get("id")
    said = take_offer("", force=yes, job_id=job_id if isinstance(job_id, int) else None)
    return jsonify(reply=said or "That one isn't waiting any more.", activity=[])


@app.post("/api/suggest")
def suggest_api():
    """💡 button: a suggestion about what's on the screen right now."""
    if not tools._screen_allowed():
        return jsonify(reply="I need permission to look at your screen first (⚙ Model → Screen).", activity=[])
    note_task()
    try:
        tip = suggest_now(force=True)
    except Exception as e:
        return jsonify(error=f"Couldn't look at the screen: {e}"), 500
    return jsonify(reply=None if tip else "I looked - nothing to suggest right now, you're doing great! 👍",
                   activity=["Looked at your screen"], shown=bool(tip))


@app.post("/api/confirm")
def confirm():
    out, status = confirm_pending(_body().get("approved") is True)
    return jsonify(out), status


@app.get("/api/events")
def get_events():
    out = events[:]
    events.clear()
    return jsonify(events=out, speaking=mouth.lips, ears=ears.status, heard=ears.heard[-1:] if settings()["wake"] else [],
                   wake_phrase=wake_phrase())


quit_hook = None          # set by desktop.py: closes the windows, tray, 3D Nova and the server


def quit_nova():
    """Shut Nova down completely (not the laptop): ears, notifications, phone, connectors, 3D Nova, then the process."""
    task_log("QUIT (asked)")
    for f in (stop_now, mouth.stop, ears.stop, notif_watcher.stop, mobile.stop, ear_close):
        try:
            f()
        except Exception:
            pass
    avatar_client.send("quit", timeout=2)
    if quit_hook:
        try:
            quit_hook()
        except Exception:
            pass
    threading.Timer(3, lambda: os._exit(0)).start()   # whatever is still running (phone, connectors) stops with us


@app.post("/api/quit")
def quit_api():
    """The chat's ⏻ Quit button."""
    threading.Timer(0.5, quit_nova).start()            # answer the page first
    return jsonify(ok=True)


@app.post("/api/stop")
def stop():
    """The Stop switch: ends the task she's working on (and her voice). Safe to press any time."""
    return jsonify(ok=True, was_busy=stop_now())


@app.get("/api/thoughts")
def get_thoughts():
    """Her thoughts after id `after` (the chat's live "💭 thinking" log). Without `after`: just where the list ends,
    so a freshly opened chat starts from now instead of replaying old requests."""
    with _thought_lock:
        last = thoughts["next"] - 1
        after = request.args.get("after", type=int)
        new = [] if after is None else [t for t in thoughts["list"] if t["id"] > after]
    return jsonify(thoughts=new, last=last, busy=lock.locked())


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
        tell_away(line)
    return jsonify(stats=s, say=say)


if __name__ == "__main__":
    tools.psutil.cpu_percent(interval=None)  # prime CPU meter
    port = PORT
    url = f"http://127.0.0.1:{port}"
    if os.environ.get("NOVA_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    if settings()["wake"]:
        ears.start()
    start_background()
    print(f"\n  {ASSISTANT_NAME} is awake at {url}  (brain: {brain()}; local: {settings()['model']} via Ollama at {llm.HOST})\n")
    app.run(host="127.0.0.1", port=port, debug=False)
