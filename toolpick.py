"""Which tools the local brain sees for one request.

All 64 tool descriptions are ~4,700 of the ~6,400 prompt tokens. On the 6 GB laptop GPU a long prompt makes every
reply slower (measured: ~9-10 tokens/s with all tools, ~16-19 with 15). So each request gets:
  CORE           - always there (opening things, windows, keys, memory, her body, PowerShell as the catch-all)
  the best match - the tools closest to the request (retrieval.scores), each with its GROUP (e.g. a web-page click
                   brings the other page tools, since a task on a page needs several of them)
  last request's - tools used in the previous request, so "now close it" / "again" still work
  more_tools     - if she still lacks something, she asks for it in words and gets the matching tools next turn

The set is chosen once per request and kept for the whole task: Ollama re-reads the prompt from the first changed
token, so changing tools between steps would throw away its cache every step.
Cloud brains get every tool (they have room, and they are smart enough not to get confused by 64).
"""
import retrieval
import tools

CORE = ["get_datetime", "get_system_status", "open_app", "open_website", "open_path", "list_windows", "window_action",
        "press_keys", "type_text", "run_powershell", "react", "update_profile", "learn_lesson", "recall_knowledge",
        "more_tools", "go_to_sleep"]

GROUPS = [
    {"list_clickable", "fill_in", "click_nth", "click_element", "scroll", "video_control", "media_control"},  # a page
    {"list_folder", "search_files", "read_text_file", "write_text_file", "move_or_rename", "copy_path",
     "create_folder", "delete_to_recycle_bin"},                                                              # files
    {"find_large_files", "temp_files_size", "clean_temp_files", "empty_recycle_bin", "preview_organize_folder",
     "organize_folder"},                                                                                     # space
    {"search_installable_apps", "install_app", "uninstall_app", "list_installed_apps", "list_startup_apps"},  # apps
    {"add_note", "list_notes", "set_reminder"},
    {"remember_fact", "forget_fact", "update_profile", "learn_lesson", "recall_knowledge"},
    {"look_at_screen", "look_through_camera", "take_screenshot"},
    {"list_top_processes", "kill_process", "get_system_status", "network_info"},
    {"set_volume", "media_control"},
    {"get_clipboard", "set_clipboard"},
    {"window_action", "minimize_all_windows", "close_all_windows", "drag_window", "list_windows"},
    {"draft_email", "write_document", "show_finished_work", "background_jobs"},                            # co-worker
    {"run_skill", "save_skill", "list_skills", "delete_skill"},                                              # skills
]

# What people actually say, so a short request finds its tool ("make it louder" -> set_volume).
HINTS = {
    "set_volume": "volume louder quieter sound mute unmute turn it up down",
    "media_control": "play pause next song previous track music spotify",
    "set_brightness": "brightness screen dimmer brighter",
    "video_control": "youtube video full screen pause play next video captions subtitles",
    "open_website": "open youtube google netflix website search the web",
    "fill_in": "search for something on a website, type in the search box",
    "click_nth": "play the first video, open the second result",
    "list_clickable": "what can I click on this page, buttons links",
    "look_at_screen": "what's on my screen, read this error, look at this",
    "look_through_camera": "how do I look, what am I holding, see me",
    "network_info": "internet wifi connected ip speed",
    "get_system_status": "battery charge cpu ram memory disk",
    "list_top_processes": "what is slowing my laptop, using memory, hogging cpu",
    "kill_process": "close force quit end task kill app",
    "find_large_files": "free up space, disk full, big files",
    "clean_temp_files": "clean up junk temp files, free space",
    "add_note": "write this down, note that, jot",
    "set_reminder": "remind me in minutes, alarm, timer",
    "take_screenshot": "screenshot capture the screen",
    "power_action": "shut down turn off restart sleep the laptop computer pc",
    "close_all_windows": "close all windows, close everything, close all apps tabs",
    "lock_screen": "lock my laptop",
    "set_dark_mode": "dark mode light mode theme",
    "toggle_wifi": "turn wifi on off",
    "drag_window": "move this window to the left right side",
    "react": "dance laugh clap wave",
    "remember_fact": "remember that, don't forget",
    "forget_fact": "forget that, delete what you know",
    "search_files": "find my file, where is the document",
    "install_app": "install download an app program",
    "uninstall_app": "uninstall remove an app program",
    "read_notifications": "read my notifications, any new messages, what did I miss, alerts, whatsapp mail",
    "draft_email": "draft write compose an email mail gmail to someone in the background",
    "write_document": "write a document letter notes essay list report in the background, make a doc",
    "show_finished_work": "show me the email, show it, open what you wrote, let me see the draft",
    "background_jobs": "is the email ready, what are you working on, background jobs status",
    "save_skill": "when I say this do that, learn this routine, teach you a new skill, remember how to do it, shortcut",
    "list_skills": "what skills routines do you know, what have I taught you",
    "delete_skill": "forget that routine, delete the skill",
    "run_skill": "run my routine, do the usual, use your skill",
}

TOP = 6           # best-matching tools per request
GROUPED = 2       # ...of which the best two bring their whole group
MORE = 8          # tools added when she asks with more_tools

BY_NAME = {t["name"]: t for t in tools.TOOLS}


def _text(t):
    return f"{t['name'].replace('_', ' ')}: {t['description']} {HINTS.get(t['name'], '')}"


def _expand(names):
    out = set(names)
    for n in names:
        for g in GROUPS:
            if n in g and len(g) <= 8:
                out |= g
    return out


def best(query, n, exclude=()):
    cands = [t for t in tools.TOOLS if t["name"] not in exclude and t["name"] != "more_tools"]
    s = retrieval.scores(query, [_text(t) for t in cands])
    ranked = sorted(zip(s, (t["name"] for t in cands)), reverse=True)
    return [name for score, name in ranked[:n] if score > 0]


def pick(query, keep=()):
    """Names of the tools for this request, in the fixed TOOLS order (so equal sets give an identical prompt)."""
    chosen = set(CORE) | set(n for n in keep if n in BY_NAME)
    top = best(query, TOP, exclude=chosen)
    chosen |= set(top) | _expand(top[:GROUPED])
    return [t["name"] for t in tools.TOOLS if t["name"] in chosen]


def add(have, need):
    """more_tools: the names to add for `need`, and the full new list."""
    new = _expand(best(need, MORE, exclude=set(have)))
    new -= set(have)
    return sorted(new), [t["name"] for t in tools.TOOLS if t["name"] in set(have) | new]


def schemas(names):
    return [BY_NAME[n] for n in names if n in BY_NAME]
