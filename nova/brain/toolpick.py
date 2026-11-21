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
from nova.actions import registry, tools

# Always offered: the tools declared with @tool(core=True) (nova/actions/registry.py), filled by _install below.
CORE = []

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
     "stop_background_job", "start_coding_job", "commit_coding_job", "retry_coding_job", "research_report"},  # co-worker
    {"run_skill", "save_skill", "edit_skill", "undo_skill_change", "save_task_as_skill", "list_skills", "delete_skill",
     "start_watching", "stop_watching"},                                              # skills
    {"react", "new_move", "forget_move", "change_look"},                                                     # 3D body
    {"fill_form", "help_login", "login_if_needed", "wait_for_code", "click_element", "list_clickable", "open_website"},         # forms, logins
]

# What people actually say, so a short request finds its tool ("make it louder" -> set_volume).
# What people actually say, so a short request finds its tool ("make it louder" -> set_volume): each tool's
# @tool(hints=...), filled by _install below.
HINTS = {}


def _install(spec):
    """A tool declared with @registry.tool brings its own CORE / GROUPS / HINTS entry (nova/actions/registry.py)."""
    if spec.core and spec.name not in CORE:
        CORE.append(spec.name)
    if spec.group:
        GROUPS.append({spec.name, *spec.group})
    if spec.hints:
        HINTS[spec.name] = spec.hints


registry.on_register(_install)

TOP = 6          # best-matching tools per request
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
