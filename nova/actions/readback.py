"""Check a step by reading its result back (manager plan 4c, the whole laptop).

The worker's or the brain's "done" is never the verdict: after a step that changes something, code looks at the
result from somewhere else - the file on disk, the window list, the setting read again. (OpenAdapt's tests: the agent
claimed success on all 30 bad saves; only reading back caught them. Nova's own task exam, 2026-11-23: "File saved"
with no file.)

    read_back(tool, args, out, started=None) -> None for a step that changes nothing (reading, listing, looking),
                                  else {"state": "passed" | "failed" | "skipped", "why": "...", "target": "..."}
                                  `started` (time.time() before the step ran) tells a new download from an old file.

A step this table has no read-back for is "skipped" with the reason - never left out, never counted as passed.
`target` names what was checked (a path, a window), so a later step that fixes the same thing replaces the verdict.
"""
import re
import time
from pathlib import Path

from nova.actions import checks
from nova.actions.skills import format as skillformat

KEEP_TEXT = 60               # the start of the written text that must be in the file
READ_MAX = 1_000_000         # bytes of a file read back
DOWNLOAD_WAIT = 10.0         # seconds a download that is still coming in gets to finish
SENT_WAIT = 6.0              # seconds a sent mail gets to show up in Sent
FORM_WAIT = 4.0              # seconds the page gets to show it took the form
POLL = 1.0                   # seconds between looks while waiting
CLOCK_SLACK = 2.0            # a file stamped this much before the step started still counts as new

# Changes nothing by name, beyond the skills' READ_ONLY prefixes (no read-back needed).
QUIET = {"react", "new_move", "go_to_sleep", "more_tools", "escalate", "start_watching", "stop_watching",
         "remember", "add_note", "set_reminder", "web_research", "research_in_background", "hand_off_job",
         "background_jobs", "stop_background_job", "research_report", "list_windows", "media_control",
         "ask_user", "note_fact"}
SELF_CHECKED = {"run_skill": "the skill checks its own steps",
                "undo_last_change": "the undo checks what it put back and says so",
                "start_coding_job": "the worker job is checked when it ends",
                "commit_coding_job": "git says what was committed",
                "retry_coding_job": "the worker job is checked when it ends"}


def _resolve(path):
    from nova.actions.tools import common
    return common._resolve(path)


def _passed(why, target=""):
    return {"state": "passed", "why": why, "target": target}


def _failed(why, target=""):
    return {"state": "failed", "why": why, "target": target}


def _skipped(why, target=""):
    return {"state": "skipped", "why": why, "target": target}


# ------------------------------------------------------------------ files

def _file_has(p, text):
    try:
        with open(p, "rb") as f:
            data = f.read(READ_MAX).decode("utf-8", errors="replace")
    except OSError as e:
        return None, f"couldn't read it back ({e.strerror or e})"
    want = " ".join(str(text or "").split())[:KEEP_TEXT]
    return (not want or want in " ".join(data.split())), ""


def write_text_file(args, out):
    p = _resolve(args.get("path", ""))
    if not p.is_file():
        return _failed(f"{p.name} is missing", str(p))
    if p.stat().st_size == 0 and str(args.get("content") or ""):
        return _failed(f"{p.name} is empty", str(p))
    ok, err = _file_has(p, args.get("content"))
    if ok is None:
        return _skipped(err, str(p))
    return _passed(f"{p.name} has the text", str(p)) if ok else _failed(f"{p.name} doesn't hold what was written", str(p))


def _moved_to(args, out):
    """The real destination: the tool's own 'Moved A -> B', else destination (/ the source's name for a folder)."""
    if " -> " in str(out):
        return Path(str(out).split(" -> ", 1)[1].strip().rstrip("."))
    src, dst = _resolve(args.get("source", "")), _resolve(args.get("destination", ""))
    return dst / src.name if dst.is_dir() else dst


def move_or_rename(args, out):
    src, dst = _resolve(args.get("source", "")), _moved_to(args, out)
    if not dst.exists():
        return _failed(f"{dst.name} is missing", str(dst))
    if src.exists():
        return _failed(f"{src.name} is still there", str(dst))
    return _passed(f"{dst.name} is there and {src.name} is gone", str(dst))


def copy_path(args, out):
    dst = _moved_to(args, out)
    return _passed(f"{dst.name} is there", str(dst)) if dst.exists() else _failed(f"{dst.name} is missing", str(dst))


def create_folder(args, out):
    p = _resolve(args.get("path", ""))
    if str(out).startswith("Nothing created"):        # it was already there: no new folder was made
        return _failed(f"no new folder was made ({p.name} was already there)", str(p))
    return _passed(f"{p.name} is there", str(p)) if p.is_dir() else _failed(f"{p.name} is missing", str(p))


def delete_to_recycle_bin(args, out):
    p = _resolve(args.get("path", ""))
    return _failed(f"{p.name} is still there", str(p)) if p.exists() else _passed(f"{p.name} is gone", str(p))


# ------------------------------------------------------------------ windows and settings

def _check(name, arg, ctx=None, timeout=3.0):
    ok, seen = checks.evaluate({"check": name, "arg": arg}, ctx or {}, timeout=timeout)
    return ok, seen


def open_app(args, out):
    from nova.actions.tools import apps
    name = str(args.get("name") or "").strip()
    if not name:
        return _skipped("no app name")
    target = apps.APPS.get(name.lower(), name)
    for q in dict.fromkeys([name, str(target).removesuffix(".exe")]):
        ok, seen = _check("window_open", q, timeout=5.0)          # a slow app (VS Code) gets its few seconds
        if ok:
            return _passed(seen, name)
    return _failed(f"no {name} window is open", name)


def window_action(args, out):
    name, action = str(args.get("title_or_app") or ""), str(args.get("action") or "focus").lower()
    if action == "focus":
        ok, seen = _check("front_window", name)
        return _passed(seen, name) if ok else _failed(seen, name)
    if action == "close":
        ok, seen = checks.evaluate({"check": "window_open", "arg": name}, {}, timeout=0)
        return _failed(f"{name} is still open", name) if ok else _passed(f"{name} is closed", name)
    return _skipped(f"no read-back for '{action}' yet", name)


def type_text(args, out):
    text = " ".join(str(args.get("text") or "").split())[:40]
    if not text:
        return _skipped("nothing was typed")
    win = str(args.get("into_window") or "")
    ok, seen = _check("page_has", text, {"window": win}, timeout=2.0)
    # Not seen isn't proof: some apps don't show their text to the screen reader (checks.py: a check that can't look
    # is not evidence of failure). So it's skipped, with why, and the Judge's goal checks still decide.
    return _passed(seen, win) if ok else _skipped(f"couldn't see the text in {win or 'the window in front'}", win)


def set_dark_mode(args, out):
    on = args.get("enabled") in (True, "true", "on", 1)
    ok, seen = _check("dark_mode", "on" if on else "off", timeout=1.0)
    return _passed(f"Windows is {seen}", "dark mode") if ok else _failed(f"Windows is still {seen}", "dark mode")


def set_brightness(args, out):
    from nova.actions.tools import common
    want = max(0, min(100, int(args.get("percent") or 0)))
    now = str(common._ps("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness") or "")
    digits = [int(x) for x in now.split() if x.strip().isdigit()]
    if not digits:
        return _skipped("couldn't read the brightness back", "brightness")
    return (_passed(f"brightness is {digits[0]}%", "brightness") if abs(digits[0] - want) <= 2
            else _failed(f"brightness is {digits[0]}%, not {want}%", "brightness"))


def wifi_on():
    """Is the Wi-Fi adapter switched on? None if it can't be read. (Also undo's "before".)"""
    from nova.actions.tools import common
    now = str(common._ps('netsh interface show interface name="Wi-Fi"') or "").lower()
    if "administrative state" not in now:
        return None
    return "enabled" in now.split("administrative state", 1)[1][:40]


def toggle_wifi(args, out):
    on = args.get("on") in (True, "true", "on", 1)
    is_on = wifi_on()
    if is_on is None:
        return _skipped("couldn't read the Wi-Fi state back", "wi-fi")
    return _passed(f"Wi-Fi is {'on' if is_on else 'off'}", "wi-fi") if is_on == on else \
        _failed(f"Wi-Fi is still {'on' if is_on else 'off'}", "wi-fi")


def set_wallpaper(args, out):
    from nova import platform
    want = _resolve(args.get("image_path", ""))
    now = platform.current().wallpaper()
    if now is None:
        return _skipped("couldn't read the wallpaper back", "wallpaper")
    same = str(Path(now)).lower() == str(want).lower()
    return _passed(f"the wallpaper is {want.name}", "wallpaper") if same else \
        _failed(f"the wallpaper is still {Path(now).name or 'none'}", "wallpaper")


# ------------------------------------------------------------------ downloads

PARTIAL = (".crdownload", ".part", ".partial", ".download", ".opdownload")   # Chrome/Edge, Firefox, Safari, Opera
DOWNLOAD_RX = re.compile(r"download", re.I)


def _downloads():
    from nova import platform
    found = platform.current().known_folder("Downloads")
    return Path(found) if found else None


def _born(p):
    st = p.stat()
    return max(st.st_mtime, st.st_ctime)          # ctime is the creation time on Windows


def _download_state(p, started):
    """-> (state, why) for one file; state None while it is still coming in."""
    if p.suffix.lower() in PARTIAL or any(p.with_name(p.name + x).exists() for x in PARTIAL):
        return None, f"{p.name} is still downloading (not finished)"
    if not p.is_file():
        return "failed", f"{p.name} is missing"
    if p.stat().st_size == 0:
        return "failed", f"{p.name} is empty (0 bytes)"
    if started and _born(p) < started - CLOCK_SLACK:
        return "failed", f"{p.name} is an old file, there before this step"
    return "passed", f"{p.name} is downloaded ({p.stat().st_size:,} bytes)"


def _newest_since(folder, started):
    new = [f for f in folder.iterdir() if f.is_file() and _born(f) >= started - CLOCK_SLACK]
    return max(new, key=_born) if new else None


def check_download(path="", folder="", started=None, wait=None):
    """A download is done: the file (or else the newest file in the folder since `started`) is there, finished (no
    .crdownload / .part), not empty, and new. Waits up to DOWNLOAD_WAIT for one still coming in."""
    end = time.time() + (DOWNLOAD_WAIT if wait is None else wait)
    target = _resolve(path) if path else None
    where = _resolve(folder) if folder else _downloads()
    if target is None and where is None:
        return _skipped("couldn't find the Downloads folder")
    if target is None and not started:
        return _skipped("no file named and no start time, so a new download can't be told apart", str(where))
    while True:
        p = target or (_newest_since(where, started) if where.is_dir() else None)
        if p is None:
            state, why = "failed", f"no new file in {where.name or where}"
        else:
            state, why = _download_state(p, started)
        if state or time.time() >= end:
            return {"state": state or "failed", "why": why, "target": str(p or where)}
        time.sleep(POLL)


def _path_in(out):
    m = re.search(r"[A-Za-z]:\\[^\r\n\"'<>|?*]+\.[A-Za-z0-9]{1,8}", str(out or ""))
    return m.group(0).rstrip(" .") if m else ""


def download(args, out, started=None):
    """A connector's download tool: the file it names (args or its own output), else the newest in the folder."""
    path = next((str(args[k]) for k in ("path", "file", "file_path", "filename", "output", "save_as", "destination")
                 if args.get(k) and Path(str(args[k])).suffix), "") or _path_in(out)
    folder = next((str(args[k]) for k in ("folder", "directory", "dir", "save_to") if args.get(k)), "")
    return check_download(path, folder, started)


# ------------------------------------------------------------------ sent mail and messages

SEND_RX = re.compile(r"send|reply|forward", re.I)
LOOKS_RX = re.compile(r"_(?:list|get|search|read|find|show|query|fetch|count)(?:_|$)", re.I)   # "list_downloads"
NO_SENT_READER = {"telegram": "Telegram doesn't let a bot read its chat history back",
                  "phone": "Phone Link's sent messages can't be read back yet",
                  "whatsapp": "WhatsApp's sent messages can't be read back yet",
                  "sms": "sent texts can't be read back yet"}
EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _first(args, *keys):
    for k in keys:
        v = args.get(k)
        if isinstance(v, (list, tuple)):
            v = v[0] if v else ""
        if v:
            return str(v)
    return ""


def _sent_query(args, started):
    """A Gmail search for the mail just sent: Sent, since the step started, to the address, with the subject."""
    to = _first(args, "to", "recipient", "recipients", "email", "address")
    subject = " ".join(re.sub(r'["{}()]', " ", _first(args, "subject", "title")).split())[:120]
    addr = EMAIL_RX.search(to)
    if not addr and not subject:
        return "", ""
    q = ["in:sent", f"after:{int(started) - 60}" if started else "newer_than:1d"]
    if addr:
        q.append(f"to:{addr.group(0)}")
    if subject:
        q.append(f'subject:"{subject}"')
    return " ".join(q), subject or addr.group(0)


def sent_mail(args, out, started=None):
    """Read a sent mail back from Gmail's Sent (the read-only Gmail connector)."""
    from nova.actions import gmail
    if not gmail.connected():
        return _skipped("Gmail isn't connected here, so Sent can't be read back", "sent mail")
    query, what = _sent_query(args, started)
    if not query:
        return _skipped("no recipient or subject to look for in Sent", "sent mail")
    end = time.time() + SENT_WAIT
    while True:
        found = gmail.search(query, count=1)
        if found:
            return _passed(f"it is in Sent: '{found[0].get('subject') or what}'", "sent mail")
        if time.time() >= end:
            return _failed(f"'{what}' isn't in Gmail's Sent", "sent mail")
        time.sleep(POLL)


def sent_message(tool, args, out, started=None):
    t = str(tool).lower()
    if "gmail" in t:
        return sent_mail(args, out, started)
    for word, why in NO_SENT_READER.items():
        if word in t:
            return _skipped(why, "sent message")
    return _skipped("this connector has no way to read what it sent", "sent message")


# ------------------------------------------------------------------ forms

SUBMIT_RX = re.compile(r"\b(submit|send|sign ?up|register|place (?:my )?order|pay|book|apply|subscribe|post)\b", re.I)
CONFIRMED = ("thank you", "thanks", "submitted", "received", "confirmed", "confirmation", "success", "has been sent", "message sent",
             "we'll be in touch", "we will be in touch", "registered", "order number", "booking reference")


def form_submit(args, out, label):
    """After a Submit-like click: the page says it took the form. Only these words are looked for - never a field's
    value, so nothing the user typed (or a password) is read back."""
    win = str(args.get("window") or "")
    end = time.time() + FORM_WAIT
    while True:
        for word in CONFIRMED:
            ok, seen = checks.evaluate({"check": "page_has", "arg": word}, {"window": win}, timeout=0)
            if ok:
                return _passed(f"the page says '{word}' after {label}", win or label)
            if seen == "no window":
                return _skipped("couldn't see the page after the click", win or label)
        if time.time() >= end:
            return _failed(f"no confirmation on the page after {label} (no 'thank you', 'submitted', ...)", win or label)
        time.sleep(POLL)


def click(tool, args, out, started=None):
    """A click is read back when its label says what it was for: Download -> the file, Submit / Send -> the page's
    thanks. Any other click stays skipped."""
    label = str(args.get("name") or args.get("contains") or "")
    if DOWNLOAD_RX.search(label):
        return check_download(started=started)
    if SUBMIT_RX.search(label):
        return form_submit(args, out, f"clicking '{label}'")
    return _skipped("no read-back for this click (only Download and Submit-like clicks are read back)")


READERS = {"write_text_file": write_text_file, "move_or_rename": move_or_rename, "copy_path": copy_path,
           "create_folder": create_folder, "delete_to_recycle_bin": delete_to_recycle_bin, "open_app": open_app,
           "window_action": window_action, "type_text": type_text, "set_dark_mode": set_dark_mode,
           "set_brightness": set_brightness, "toggle_wifi": toggle_wifi, "set_wallpaper": set_wallpaper}
# Readers that need the tool's name or the step's start time: fn(tool, args, out, started).
TIMED = {"click_element": click, "click_nth": click}
NOT_READABLE = {"press_keys": "keys can't be read back", "set_volume": "the volume can't be read back yet",
                "click_at": "no read-back for a click at a point (nothing says what was clicked)",
                "scroll": "nothing to read back",
                "fill_form": "it fills the boxes but never submits, and the values stay private (not read back); "
                             "the Submit click is checked",
                "help_login": "the user signs in themself; the step says whether the page moved on",
                "draft_email": "a draft is written in the background and the user sends it themself"}


def _by_name(tool):
    """A connector's tool (mcp_<server>_<tool>) is read back by what its name says it does."""
    t = str(tool)
    if not t.startswith("mcp_") or LOOKS_RX.search(t):
        return None
    if DOWNLOAD_RX.search(t):
        return lambda tool, args, out, started: download(args, out, started)
    if SEND_RX.search(t):
        return sent_message
    return None


def changes_things(tool):
    t = str(tool or "")
    return not (t.startswith(skillformat.READ_ONLY) or t in QUIET)


def read_back(tool, args, out, started=None):
    """The read-back for one finished step (see the module doc). Never raises."""
    if not changes_things(tool):
        return None
    if tool in SELF_CHECKED:
        return _skipped(SELF_CHECKED[tool])
    fn, timed = READERS.get(tool), TIMED.get(tool) or _by_name(tool)
    if fn is None and timed is None:
        return _skipped(NOT_READABLE.get(tool, "no read-back for this step yet"))
    try:
        if timed is not None:
            return timed(tool, dict(args or {}), out, started)
        return fn(dict(args or {}), out)
    except Exception as e:
        return _skipped(f"couldn't read it back ({type(e).__name__})")


def note(rb):
    """The line the brain reads under the step's result."""
    if not rb:
        return ""
    return {"passed": f"\n[check: passed - {rb['why']}]", "failed": f"\n[check: FAILED - {rb['why']}. It is NOT done.]",
            "skipped": f"\n[check: skipped - {rb['why']}]"}[rb["state"]]
