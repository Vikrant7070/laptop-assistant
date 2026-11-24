"""Nova's tools, one module per area (Part B, Step D).

Each tool is a function declared with @tool (nova/actions/registry.py) in one of the modules below. This package
holds the tables built from those declarations (TOOLS, FUNCS, RISKY, DANGEROUS) and run_tool().

`tools.<name>` still works for any function or constant of any module (app.py, skills.py and friends use it): it is
looked up in its home module on every access. Tests must patch the home module (`tools.windows._windows`), never
`tools` itself - tests/test_tools_package.py checks that.
"""


from nova.actions import registry


# Declaration order = the order the brain sees the tools in.
from nova.actions.tools import common, system, apps, files, windows, forms, eyes, notes, routines, body, terminal, taskcard  # noqa: E402,F401


TOOLS = []          # filled from the @tool declarations (see _install below)


# Approval tiers, used by the autonomy setting in app.py:
#   careful -> asks before RISKY and DANGEROUS;  trusted -> asks only before DANGEROUS;  full -> never asks.
RISKY = set()          # filled from @tool(risky=...)


# run_skill is checked step by step in app.is_risky: a routine needs an OK when any of its steps would.
DANGEROUS = set()      # filled from @tool(dangerous=True)


def is_dangerous(name, args):
    if name in DANGEROUS:
        return True
    if name == "power_action" and args.get("action") in ("shutdown", "restart"):
        return True
    return name == "run_powershell" and args.get("as_admin") in (True, "true")


FUNCS = {}

# Tools whose results are text other people wrote: run_tool wraps them with protocol.untrusted (roadmap R2 step 4).
UNTRUSTED = {"read_email", "check_email", "read_page", "web_research", "read_notifications"}


def _install(spec):
    """A tool declared with @registry.tool joins the tables above (nova/actions/registry.py)."""
    if spec.name in FUNCS:
        raise ValueError(f"tool {spec.name!r} is both in TOOLS and declared with @tool")
    TOOLS.append(spec.schema())
    FUNCS[spec.name] = spec.fn
    if spec.risky:
        RISKY.add(spec.name)
    if spec.dangerous:
        DANGEROUS.add(spec.name)


registry.on_register(_install)

from nova.actions import speedup  # noqa: E402,F401  (its @tool declarations join the tables above)


def run_tool(name, args):
    if name not in FUNCS:
        return f"Error in {name}: no such tool."
    try:
        import inspect
        allowed = inspect.signature(FUNCS[name]).parameters
        args = dict(args or {})
        extra = [k for k in args if k not in allowed]
        missing = [k for k, p in allowed.items() if p.default is inspect.Parameter.empty and k not in args
                   and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
        if len(extra) == 1 and len(missing) == 1:     # {"query": ...} for topic: the small model misnamed the one it needs
            args[missing[0]] = args.pop(extra[0])
        args = {k: v for k, v in args.items() if k in allowed}  # local models sometimes add extras
        out = str(FUNCS[name](**args))
        if name in UNTRUSTED and not out.startswith(f"Error in {name}"):
            from nova import protocol
            out = protocol.untrusted(name, out)
        return out
    except Exception as e:
        return f"Error in {name}: {e}"


_MODULES = {m.__name__.rsplit(".", 1)[1]: m for m in (common, system, apps, files, windows, forms, eyes, notes, routines, body, terminal, taskcard)}
_HOME = {name: _MODULES[mod] for mod, names in {
    'common': ['ANGLE_HELP', 'B', 'HOME', 'N', 'S', '_files_lock', '_fmt_bytes', '_load', '_ps', '_resolve', '_save'],
    'system': ['SETTINGS_PAGES', 'UI_WAYS', '_clear_notifications', '_like_literal', '_nova_pids', 'get_datetime', 'get_system_status', 'install_app', 'kill_process', 'list_installed_apps', 'list_top_processes', 'lock_screen', 'media_control', 'network_info', 'open_settings_page', 'power_action', 'run_powershell', 'search_installable_apps', 'set_brightness', 'set_dark_mode', 'set_volume', 'set_wallpaper', 'stats', 'ui_way', 'uninstall_app'],
    'apps': ['APPS', 'BROWSERS', 'RESEARCH_SITES', '_START_CACHE', '_app_path_exe', '_browser_in_use', '_chrome_exe', '_exe_path', '_installed_names', '_search_results', '_start_menu_apps', '_start_menu_match', '_wait_for_window', 'find_app', 'open_app', 'open_path', 'open_website', 'open_windows_summary', 'page_links', 'page_text', 'read_page', 'web_research'],
    'files': ['CATEGORIES', '_organize_plan', 'clean_temp_files', 'copy_path', 'create_folder', 'delete_to_recycle_bin', 'empty_recycle_bin', 'find_large_files', 'list_folder', 'move_or_rename', 'organize_folder', 'preview_organize_folder', 'read_text_file', 'search_files', 'temp_files_size', 'write_text_file'],
    'windows': ['APP_PROCS', 'CAPTION_NAMES', 'CAPTION_SLOT', 'NEW_WINDOW_KEYS', 'SELECTION_MAX', 'SKIP_PROCS', 'SWITCH_KEYS', '_NAMED_APP_RE', '_app_window', '_asks_to_save', '_caption_button', '_clip_restore', '_clip_save', '_clip_set_text', '_done', '_find_window', '_focus', '_focused_text', '_frame', '_keys_combo', '_own_pids', '_point_at_focus', '_stop_pointing', '_type_into_focus', '_wait_for_paste', '_window_by_hand', '_windows', 'app_guard', 'close_all_windows', 'get_clipboard', 'list_windows', 'minimize_all_windows', 'named_app', 'press_keys', 'read_selection', 'set_clipboard', 'take_screenshot', 'type_text', 'window_action', 'window_is_app'],
    'forms': ['CLICKABLE', 'LOGIN_RX', 'NO_SECRETS', 'VIDEO_BUTTONS', 'VIDEO_SITES', '_box_value', '_bring_into_view', '_focused_is_password', '_form_boxes', '_gone', '_is_password', '_mouse_click', '_move_mouse_smoothly', '_nova_click', '_scan', '_target_window', '_title_of', '_video_window', 'click_at', 'click_element', 'click_nth', 'fill_form', 'fill_in', 'help_login', 'list_clickable', 'scroll', 'video_control', 'wait_for_code'],
    'eyes': ['LAST_IMAGE', 'SCREEN_HOOKS', '_camera_allowed', '_camera_users', '_grab_camera_jpeg', '_grab_screen_jpeg', '_screen_allowed', '_setting', 'look_at_screen', 'look_through_camera', 'toggle_wifi'],
    'notes': ['EMAIL_WARN', 'LOCAL_TOPIC', '_ago', '_email_gate', '_norm_all', 'add_note', 'background_jobs', 'check_email', 'commit_coding_job', 'connect_gmail', 'draft_email', 'due_reminders', 'email_ok', 'forget_fact', 'go_to_sleep', 'hand_off_job', 'learn_lesson', 'list_notes', 'list_reminders', 'memories', 'mute_notifications', 'read_email', 'read_notifications', 'recall_knowledge', 'remember_fact', 'research_in_background', 'research_report', 'retry_coding_job', 'set_reminder', 'show_coding_changes', 'show_finished_work', 'start_coding_job', 'stop_background_job', 'update_profile', 'write_document'],
    'routines': ['CONTINUE_NAME', 'SIGNIN_NAME', '_buttons', 'delete_skill', 'edit_skill', 'escalate', 'forms_code_box', 'list_skills', 'login_if_needed', 'more_tools', 'run_skill', 'save_skill', 'save_task_as_skill', 'start_watching', 'stop_watching', 'undo_skill_change'],
    'body': ['can_do', 'change_look', 'forget_move', 'new_move', 'react', 'reaction_name'],
    'taskcard': ['YES', '_card', 'ask_user', 'note_fact'],
    'terminal': ['TERM_SCRIPT', 'TERM_TITLE', 'TERM_WAIT', '_avatar_pos', '_open_terminal', '_read_text', '_terminal', 'drag_window', 'run_in_terminal'],
}.items() for name in names}


def __getattr__(name):
    """tools.<name> -> the current value in its home module (so patches there are seen by every caller)."""
    mod = _HOME.get(name)
    if mod is None:
        for m in _MODULES.values():          # an imported name a module uses (psutil, keyboard, webbrowser...)
            if hasattr(m, name):
                return getattr(m, name)
        raise AttributeError(f"module 'nova.actions.tools' has no attribute {name!r}")
    return getattr(mod, name)


def home_of(name):
    """The module that owns `name` (for tests and tools/__init__ itself)."""
    return _HOME.get(name)
