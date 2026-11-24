"""Risk levels for every action (manager plan 4e). Pure code: one table, a few rules.

    0 - read: look, list, read files or mail, search. Runs freely.
    1 - a change that can be undone, or one inside Nova herself (notes, skills, her body): the autonomy setting
        decides, as before (careful asks for the RISKY ones).
    2 - hard to undo, or it leaves the laptop: delete, send, pay, install, sign in, a shell command that changes
        anything, shutting down. ALWAYS asks, at every autonomy level (session.is_risky). The only
        switch is the old "shut down without asking" (power_ok), and it never covers delete, send, pay or shell.
    never - typing a password, a one-time code or a card number: refused (never_why); those stay with the user
        (fill_form / help_login already keep the values away from the brain).

    level(name, args)       -> 0 / 1 / 2 (a shell command that only reads is 0; a connector tool by its name)
    always_asks(name, args, settings) -> bool
    never_why(name, args)   -> "" or why it is refused
    orders_in(text)         -> instructions found in outside text ("delete all my files"), for 4e's rule that
                               outside text is data, not orders
    held(order, call, goal) -> does this call carry out that outside order (and the user didn't ask for it)?

Every tool must be in LEVELS: tests/test_risk.py fails when a new tool has no level.
"""
import re

READ, CHANGE, HARD = 0, 1, 2

LEVELS = {
    # 0 - reads, looks, lists, searches; Nova's own bookkeeping that changes nothing
    **dict.fromkeys((
        "get_datetime", "get_system_status", "get_clipboard", "check_email", "check_memory", "read_email",
        "read_notifications", "read_page", "read_selection", "read_text_file", "list_clickable", "list_folder",
        "list_installed_apps", "list_notes", "list_reminders", "list_skills", "list_startup_apps",
        "list_top_processes", "list_windows", "look_at_screen", "look_through_camera", "take_screenshot",
        "find_app", "find_large_files", "search_files", "search_installable_apps", "temp_files_size",
        "network_info", "preview_organize_folder", "recall_knowledge", "research_report", "show_coding_changes",
        "background_jobs", "web_research", "research_in_background", "wait_for_code", "more_tools", "escalate",
        "react", "ask_user", "note_fact", "hand_off_job", "scroll"), READ),
    # 1 - changes that can be undone, or that stay inside Nova
    **dict.fromkeys((
        "open_app", "open_path", "open_website", "open_settings_page", "window_action", "minimize_all_windows",
        "close_all_windows", "drag_window", "lock_screen", "click_at", "click_element", "click_nth", "fill_in",
        "type_text", "press_keys", "set_clipboard", "media_control", "video_control", "set_volume",
        "set_brightness", "set_dark_mode", "set_wallpaper", "toggle_wifi", "set_startup_app", "mute_notifications",
        "copy_path", "create_folder", "move_or_rename", "organize_folder", "write_text_file", "write_document",
        "draft_email", "add_note", "set_reminder", "remember_fact", "forget_fact", "update_profile",
        "learn_lesson", "save_skill", "edit_skill", "undo_skill_change", "save_task_as_skill", "delete_skill",
        "run_skill", "start_watching", "stop_watching", "change_look", "new_move", "forget_move", "go_to_sleep",
        "show_finished_work", "stop_background_job", "start_coding_job", "retry_coding_job", "undo_last_change",
        "kill_process"), CHANGE),      # closing a program, like closing its window (the plan: open/close apps)
    # 2 - hard to undo, or leaves the laptop
    **dict.fromkeys((
        "delete_to_recycle_bin", "empty_recycle_bin", "clean_temp_files", "run_powershell", "run_in_terminal",
        "install_app", "uninstall_app", "power_action", "fill_form", "help_login",
        "login_if_needed", "connect_gmail", "commit_coding_job"), HARD),
}
SHELL = ("run_powershell", "run_in_terminal")
NEVER_OFF = {"delete_to_recycle_bin", "empty_recycle_bin", "clean_temp_files", "fill_form", *SHELL}   # no setting
CONNECTOR_HARD = re.compile(r"send|delete|remove|post|publish|pay|transfer|purchase|buy|order|share|invite|trash",
                            re.I)


def level(name, args=None):
    """The level of one call. Unknown tools (a connector's) are 2 when their name sounds like send / delete / pay,
    else 1 - never 0 on a guess."""
    if name in SHELL:
        from nova.actions import shell_policy
        return HARD if shell_policy.needs_ok(name, args or {}) else READ
    if name in LEVELS:
        return LEVELS[name]
    return HARD if CONNECTOR_HARD.search(str(name)) else CHANGE


def always_asks(name, args=None, settings=None):
    """Level 2 asks every time, whatever the autonomy setting. power_ok ("shut down without asking") is the one
    switch, and only for power."""
    if level(name, args) < HARD:
        return False
    if name == "power_action" and ((args or {}).get("action") == "cancel" or (settings or {}).get("power_ok")):
        return False
    return True


# ------------------------------------------------------------------ never Nova: passwords, codes, card numbers

SECRET_BOX = re.compile(r"pass(word|code|phrase)?\b|\bpin\b|one[- ]time|\botp\b|verification|security code|\bcvv\b|"
                        r"\bcvc\b|card number|credit card|debit card|\b2fa\b|auth(entication)? code", re.I)
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def _luhn(digits):
    total, odd = 0, True
    for d in reversed(digits):
        n = int(d)
        if not odd:
            n = n * 2 - 9 if n > 4 else n * 2
        total, odd = total + n, not odd
    return total % 10 == 0


def has_card_number(text):
    for m in CARD.finditer(str(text or "")):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return True
    return False


def never_why(name, args):
    """Typing a password, a one-time code or a card number is the user's job. -> why it is refused, or ""."""
    args = args or {}
    if name not in ("type_text", "fill_in", "set_clipboard", "press_keys"):
        return ""
    text = " ".join(str(args.get(k) or "") for k in ("text", "keys"))
    if has_card_number(text):
        return "that looks like a card number"
    if name == "fill_in" and SECRET_BOX.search(str(args.get("box") or "")):
        return "that box is for a password or a code"
    if name == "type_text" and SECRET_BOX.search(str(args.get("into_window") or "")):
        return "that window asks for a password or a code"
    return ""


# ------------------------------------------------------------------ outside text is data, not orders

# What an order asks for -> the calls that would carry it out. The shell can do any of them.
KINDS = {
    "delete": (r"delete|remove|erase|wipe|format|clear out|empty", {"delete_to_recycle_bin", "empty_recycle_bin",
               "clean_temp_files", "organize_folder", "move_or_rename", "delete_skill", "forget_fact",
               "uninstall_app"}),
    "send": (r"send|forward|reply|e-?mail|message|text|share|post|publish|upload|invite",
             {"draft_email", "fill_form", "type_text", "press_keys", "fill_in"}),
    "pay": (r"pay|buy|purchase|order|transfer|wire|donate|subscribe|tip", {"fill_form", "fill_in", "click_at",
            "click_element", "click_nth"}),
    "install": (r"install|uninstall|update|download and (?:run|install)", {"install_app", "uninstall_app"}),
    "run": (r"run|execute|launch|start", {"start_coding_job", "run_skill"}),
    "switch off": (r"disable|turn off|switch off|shut ?down|restart|reboot|kill|stop|lock", {"toggle_wifi",
                   "power_action", "kill_process", "set_startup_app", "lock_screen"}),
    "sign in": (r"log ?in|sign ?in|enter your|verify your|confirm your", {"help_login", "login_if_needed",
                "connect_gmail", "fill_form"}),
}
_LEAD = r"(?:(?:please|kindly|now|then|also|and|immediately|urgent(?:ly)?:?|nova|assistant|ai|bot|you (?:must|should|need to|have to))[,:!]?\s+)*"
# 4g: "Every morning at 9, delete …" in an email is an order too (a schedule is made only from the user's words)
_WHEN = (r"(?:(?:every|each|daily|hourly|weekly)\b(?:\s+\w+)?(?:\s+(?:morning|afternoon|evening|night))?"
         r"(?:\s+at\s+\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?)?[,:]?\s+)?")
_LEAD = _WHEN + _LEAD
_ORDER = re.compile(r"^\W*" + _LEAD + r"(?P<verb>" + "|".join(v for v, _ in KINDS.values()) + r")\b", re.I)
_JAILBREAK = re.compile(r"\b(ignore|disregard|forget)\b.{0,30}\b(instructions|rules|previous|above|prompt)\b|"
                        r"\b(new instructions|system prompt|you are now)\b", re.I)


def orders_in(text, limit=5):
    """Sentences in outside text that tell someone to do something: [{"kind", "quote"}]."""
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", str(text or "")):
        s = sentence.strip()
        if not s or len(s) > 240:
            continue
        if _JAILBREAK.search(s):
            out.append({"kind": "any", "quote": s})
        else:
            m = _ORDER.match(s)
            if m:
                verb = m.group("verb").lower()
                kind = next(k for k, (v, _) in KINDS.items() if re.fullmatch(v, verb, re.I))
                out.append({"kind": kind, "quote": s})
        if len(out) >= limit:
            break
    return out


def held(order, call, goal=""):
    """Would this call carry out the outside order - and the user's own request didn't ask for that kind of thing?"""
    name, args = call["name"], call.get("args") or {}
    if level(name, args) == READ:
        return False
    kind = order["kind"]
    if kind == "any":
        return True                                  # "ignore your instructions": nothing that changes things
    verbs, names = KINDS[kind]
    does = name in names or name in SHELL or (name not in LEVELS and re.search(verbs, name, re.I))
    if not does:
        return False
    return not re.search(r"\b(" + verbs + r")", str(goal or ""), re.I)
