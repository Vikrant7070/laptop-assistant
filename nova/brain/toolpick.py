"""Which tools the local brain sees for one request.

All 101 tool descriptions are roughly 9,400 prompt tokens on their own - more than the local 8K window can
hold. On the 6 GB laptop GPU a long prompt also makes every reply slower (measured: ~9-10 tokens/s with
all tools, ~16-19 with 15). So each request gets:
  CORE           - always there (opening things, windows, keys, memory, her body, PowerShell as the catch-all)
  the best match - the tools closest to the request (retrieval.scores), each with its GROUP (e.g. a web-page click
                   brings the other page tools, since a task on a page needs several of them)
  last request's - tools used in the previous request, so "now close it" / "again" still work
  more_tools     - if she still lacks something, she asks for it in words and gets the matching tools next turn

The set is chosen once per request and kept for the whole task: Ollama re-reads the prompt from the first changed
token, so changing tools between steps would throw away its cache every step.
Cloud brains get every tool (they have room, and they are smart enough not to get confused by 101).
"""
import re

from nova.brain import retrieval
from nova.actions import tools

CORE = ["get_datetime", "get_system_status", "open_app", "open_website", "open_path", "list_windows", "window_action",
        "press_keys", "type_text", "run_powershell", "run_in_terminal", "react", "update_profile", "learn_lesson", "recall_knowledge",
        "more_tools", "escalate", "go_to_sleep"]

GROUPS = [
    {"list_clickable", "fill_in", "click_nth", "click_element", "scroll", "video_control", "media_control"},  # a page
    {"web_research", "read_page", "click_element", "click_nth", "scroll", "list_clickable", "open_website"},  # research
    {"list_folder", "search_files", "read_text_file", "write_text_file", "move_or_rename", "copy_path",
     "create_folder", "delete_to_recycle_bin"},                                                              # files
    {"find_large_files", "temp_files_size", "clean_temp_files", "empty_recycle_bin", "preview_organize_folder",
     "organize_folder"},                                                                                     # space
    {"find_app", "search_installable_apps", "install_app", "uninstall_app", "list_installed_apps",
     "list_startup_apps"},                                                                                   # apps
    {"add_note", "list_notes", "set_reminder", "list_reminders"},
    {"read_notifications", "mute_notifications"},
    {"check_email", "read_email", "connect_gmail"},
    {"remember_fact", "forget_fact", "update_profile", "learn_lesson", "recall_knowledge"},
    {"look_at_screen", "look_through_camera", "take_screenshot"},
    {"list_top_processes", "kill_process", "get_system_status", "network_info"},
    {"set_volume", "media_control"},
    {"get_clipboard", "set_clipboard", "read_selection"},
    {"window_action", "minimize_all_windows", "close_all_windows", "drag_window", "list_windows"},
    {"draft_email", "write_document", "research_in_background", "show_finished_work", "background_jobs",
     "stop_background_job", "start_coding_job", "commit_coding_job", "retry_coding_job", "research_report"},                                         # co-worker
    {"run_skill", "save_skill", "edit_skill", "undo_skill_change", "save_task_as_skill", "list_skills", "delete_skill",
     "start_watching", "stop_watching"},                                              # skills
    {"react", "new_move", "forget_move", "change_look"},                                                     # 3D body
    {"fill_form", "help_login", "login_if_needed", "wait_for_code", "click_element", "list_clickable", "open_website"},         # forms, logins
]

# What people actually say, so a short request finds its tool ("make it louder" -> set_volume).
HINTS = {
    "set_volume": "volume louder quieter sound mute unmute turn it up down",
    "read_selection": "read this selected highlighted text out loud what does it say read it to me",
    "media_control": "play pause next song previous track music spotify",
    "set_brightness": "brightness screen dimmer brighter",
    "video_control": "youtube video full screen pause play next video captions subtitles",
    "open_website": "open youtube google netflix website",
    "web_research": "show me google on my screen, open a google search, search it in chrome in front of me",
    "research_in_background": "research look up find out about search the web google it docs documentation github "
                              "stack overflow learn about compare best library how to tutorial in the background",
    "read_page": "read this page article website docs, what does the page say, summarise the page",
    "find_app": "do I have this app, is it installed, open an app I may not have, need an app for this",
    "fill_in": "search for something on a website, type in the search box",
    "fill_form": "fill in this form, fill out the form with my details, sign up form, application form, my address name email",
    "help_login": "log in sign in login to the site account, my password, help me log in",
    "login_if_needed": "make sure I am logged in, sign in to the site if needed, log in before continuing",
    "edit_skill": "change my skill, fix the skill, update the skill, add a step to the skill, the skill should also, undo skill change",
    "save_task_as_skill": "remember that, save that as a skill, next time do that, make that a skill, call it",
    "start_watching": "watch me, watch how I do this, let me show you, learn this from me, record what I do",
    "stop_watching": "done, I'm finished showing you, stop watching",
    "wait_for_code": "otp verification code one time password two factor 2fa sms code authenticator code enter the code",
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
    "list_reminders": "what reminders do I have, my reminders, upcoming reminders",
    "take_screenshot": "screenshot capture the screen",
    "power_action": "shut down turn off restart sleep the laptop computer pc",
    "lock_screen": "lock my laptop",
    "set_dark_mode": "dark mode light mode theme",
    "toggle_wifi": "turn wifi on off",
    "drag_window": "move this window to the left right side",
    "react": "dance laugh clap wave",
    "run_in_terminal": "git commit log status branch remote diff last commit of my project repo, ssh npm pip python "
                       "script command line terminal run a command",
    "close_all_windows": "close all windows, close everything, close all apps",
    "new_move": "do a new move, act out, shrug, salute, stretch, robot dance, jumping jacks, pose, show me how",
    "change_look": "change your look, outfit, hair colour, clothes, wear glasses, cat ears, hat, dress up, new style",
    "forget_move": "forget that move, delete your move",
    "remember_fact": "remember that, don't forget",
    "forget_fact": "forget that, delete what you know",
    "search_files": "find my file, where is the document",
    "install_app": "install download an app program",
    "uninstall_app": "uninstall remove an app program",
    "read_notifications": "read my notifications, any new messages, what did I miss, alerts, whatsapp mail",
    "check_email": "check my email gmail inbox unread mail any important emails today did someone email me find the email from",
    "read_email": "read the email open that mail summarise the email what does the email say",
    "connect_gmail": "connect gmail link my gmail sign in to gmail set up gmail for nova give nova access to my email",
    "mute_notifications": "don't read notifications from an app, stop reading ignore mute silence that app's "
                          "notifications, resume start reading unmute its notifications again",
    "draft_email": "draft write compose an email mail gmail to someone in the background",
    "write_document": "write a document letter notes essay list report in the background, make a doc",
    "show_finished_work": "show me the email, show it, open what you wrote, let me see the draft",
    "background_jobs": "is the email ready, what are you working on, background jobs status, how's the research going",
    "start_coding_job": "fix the bug in my code project repo, change the code, edit the readme, build a website "
                        "page app feature, refactor, add tests, coding job, use opencode deepseek claude code",
    "commit_coding_job": "commit it, commit the changes, commit what the worker changed, git commit",
    "show_coding_changes": "what changed, show me the changes, the diff, what did the worker change",
    "retry_coding_job": "try it with claude code, try again with the other worker, give it to claude, try the job "
                        "again, run the research again",
    "research_report": "send me the report, what did you find, the research findings, the research from tuesday, "
                       "read me the research",
    "stop_background_job": "stop the research, cancel the email, forget that job, stop working on that in the background",
    "save_skill": "when I say this do that, learn this routine, teach you a new skill, remember how to do it, shortcut",
    "list_skills": "what skills routines do you know, what have I taught you",
    "delete_skill": "forget that routine, delete the skill",
    "run_skill": "run my routine, do the usual, use your skill",
}

TOP = 6           # best-matching tools per request
GROUPED = 2       # ...of which the best two bring their whole group
MORE = 8          # tools added when she asks with more_tools

# 🔌 Connector tools (nova/actions/mcp.py) come and go while Nova runs; app.py points these at mcp.tools_list/groups.
EXTRA = lambda: []           # noqa: E731
EXTRA_GROUPS = lambda: []    # noqa: E731


def _all():
    return tools.TOOLS + EXTRA()


def _text(t):
    return f"{t['name'].replace('_', ' ')}: {t['description']} {HINTS.get(t['name'], '')}"


def _expand(names):
    out = set(names)
    for n in names:
        for g in GROUPS + EXTRA_GROUPS():
            if n in g and len(g) <= 8:
                out |= g
    return out


def best(query, n, exclude=()):
    cands = [t for t in _all() if t["name"] not in exclude and t["name"] != "more_tools"]
    s = retrieval.scores(query, [_text(t) for t in cands])
    ranked = sorted(zip(s, (t["name"] for t in cands)), reverse=True)
    return [name for score, name in ranked[:n] if score > 0]


# Coding jobs (workers.py) by plain words, not similarity: a tool's text is cut to 128 tokens for retrieval and
# start_coding_job's long description leaves its hints out - "fix the spelling mistakes in the README.md of my project"
# ranked it 22nd (live, 2026-11-21) and the brain opened Notepad instead.
CODING = re.compile(r"\b(readme|repo|repository|codebase|source code|code ?base|bugs?|refactor\w*|unit tests?|"
                    r"opencode|open code|deepseek|claude code|coding job|git|commit|"
                    r"(my|the|this|our) (code|project|website project|web ?app)( folder)?)\b", re.I)
CODING_TOOLS = {"start_coding_job", "commit_coding_job", "show_coding_changes", "retry_coding_job", "background_jobs",
                "stop_background_job"}


def pick(query, keep=()):
    """Names of the tools for this request, in the fixed TOOLS order (so equal sets give an identical prompt)."""
    every = _all()
    known = {t["name"] for t in every}
    chosen = set(CORE) | set(n for n in keep if n in known)
    if CODING.search(str(query or "")):
        chosen |= CODING_TOOLS & known
    top = best(query, TOP, exclude=chosen)
    chosen |= set(top) | _expand(top[:GROUPED])
    return [t["name"] for t in every if t["name"] in chosen]


def add(have, need):
    """more_tools: the names to add for `need`, and the full new list."""
    new = _expand(best(need, MORE, exclude=set(have)))
    new -= set(have)
    return sorted(new), [t["name"] for t in _all() if t["name"] in set(have) | new]


def schemas(names):
    by_name = {t["name"]: t for t in _all()}
    return [by_name[n] for n in names if n in by_name]
