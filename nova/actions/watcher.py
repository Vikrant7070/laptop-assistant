"""🆘 Nova notices trouble on the screen and offers to help - once, without nagging.

Every few seconds (no screenshot, no AI - just asking Windows about its windows, a few milliseconds):
  - a new error box: a dialog window (class #32770, the standard Windows message box / task dialog) whose title or
    text says error / failed / can't / not found / stopped working ...  -> "X shows an error: '...'. Want help?"
  - an app that stopped responding for 10 s (Windows' own IsHungAppWindow test) -> "X isn't responding. Close it?"
Each error / frozen window is offered once. Nova's own windows and anything already open when she starts are ignored.
The error text goes to her brain only if the user says yes.
"""
import re
import time

import psutil

ERROR_RE = re.compile(
    r"\b(error|errors|failed|failure|fatal|exception|crash(ed)?|stopped working|not responding|cannot|can'?t|"
    r"couldn'?t|could not|unable to|denied|not found|is missing|was not found|invalid|corrupt(ed)?|"
    r"problem|went wrong|unexpected)\b", re.I)
SKIP_RE = re.compile(r"(do you want to save|save changes|want to save|are you sure|confirm|replace (it|the)|overwrite|"
                     r"sign in|password|update available|restart now)", re.I)
NOVA_PROCS = {"python.exe", "pythonw.exe"}
NEVER_CLOSE = {"explorer.exe", "dwm.exe", "csrss.exe", "winlogon.exe", "python.exe", "pythonw.exe"}
HUNG_SECS = 10
MAX_TEXT = 300

def _desk():
    from nova import platform
    return platform.current()


def _text_of(hwnd):
    return _desk().title(hwnd)


def _class_of(hwnd):
    return _desk().class_name(hwnd)


def top_windows():
    """Visible top-level windows: (hwnd, class, title, pid, process name) (nova.platform)."""
    return _desk().top_level()


def dialog_text(hwnd):
    """The message inside a dialog. Classic message boxes keep it in Static children; task dialogs (the newer style)
    draw it themselves, so UI Automation reads those."""
    parts = [t.strip() for _, cls, t, visible in _desk().children(hwnd) if cls == "Static" and visible and t.strip()]
    if not parts:
        try:
            import uiautomation as uia
            ctl = uia.ControlFromHandle(hwnd)
            for c, _ in uia.WalkControl(ctl, maxDepth=5):
                if c.ControlTypeName == "TextControl" and c.Name and c.Name.strip():
                    parts.append(c.Name.strip())
                if len(parts) >= 6:
                    break
        except Exception:
            pass  # intentional: a window that will not answer is skipped
    text = " ".join(dict.fromkeys(parts))           # in order, without repeats
    return re.sub(r"\s+", " ", text)[:MAX_TEXT]


def is_hung(hwnd):
    return _desk().is_hung(hwnd)


def short_app(proc, title):
    name = re.sub(r"\.exe$", "", proc or "", flags=re.I)
    return {"winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "msedge": "Edge", "chrome": "Chrome",
            "code": "VS Code", "notepad": "Notepad", "werfault": "Windows", "powershell": "PowerShell",
            "pwsh": "PowerShell", "explorer": "File Explorer", "acrord32": "Acrobat Reader"}.get(name.lower(), name or title or "An app")


class Watcher:
    """Remembers what it has already offered. scan() returns the new things worth offering help with."""

    def __init__(self, windows=top_windows, read=dialog_text, hung=is_hung, clock=time.time):
        self.windows, self.read, self.hung, self.clock = windows, read, hung, clock
        self.seen = set()              # (kind, process, text) offered or ignored already
        self.known = None              # dialogs open when Nova started: never offered
        self.hung_since = {}

    def scan(self):
        now = self.clock()
        wins = self.windows()
        found = []
        dialogs = {w[0] for w in wins if w[1] == "#32770"}
        if self.known is None:
            self.known = dialogs
            return []
        self.known &= dialogs           # a closed startup dialog's handle may be reused later
        for hwnd, cls, title, pid, proc in wins:
            if proc.lower() in NOVA_PROCS:
                continue
            if cls == "#32770" and hwnd not in self.known:
                text = self.read(hwnd)
                both = f"{title} {text}"
                key = ("error", proc.lower(), (text or title)[:120])
                if key in self.seen:
                    continue
                if ERROR_RE.search(both) and not SKIP_RE.search(both):
                    self.seen.add(key)
                    found.append({"kind": "error", "hwnd": hwnd, "pid": pid, "proc": proc, "title": title,
                                  "text": text or title, "app": short_app(proc, title)})
                else:
                    self.seen.add(key)    # an ordinary dialog: look at it only once
            elif cls != "#32770" and title and proc.lower() not in NEVER_CLOSE:
                if self.hung(hwnd):
                    first = self.hung_since.setdefault(hwnd, now)
                    key = ("hung", hwnd)
                    if now - first >= HUNG_SECS and key not in self.seen:
                        self.seen.add(key)
                        found.append({"kind": "hung", "hwnd": hwnd, "pid": pid, "proc": proc, "title": title,
                                      "text": "", "app": short_app(proc, title)})
                else:
                    self.hung_since.pop(hwnd, None)
                    self.seen.discard(("hung", hwnd))      # it recovered: offer again if it freezes again later
        return found


def still_there(item):
    """Is it still worth offering? The box is still open / the app is still frozen."""
    try:
        if not _desk().is_window(item["hwnd"]) or not _desk().is_visible(item["hwnd"]):
            return False
        return is_hung(item["hwnd"]) if item["kind"] == "hung" else True
    except Exception:
        return False


def offer_line(item):
    if item["kind"] == "hung":
        return (f"{item['app']} isn't responding. Want me to close it? Anything unsaved in it would be lost.",
                f"{item['app']} isn't responding. Should I close it?")
    msg = item["text"]
    msg = msg if len(msg) <= 140 else msg[:137] + "..."
    return (f"{item['app']} shows an error: “{msg}”. Want me to help with it?",
            f"{item['app']} just showed an error. Want me to help with it?")


def help_request(item):
    """What the user's 'yes' asks Nova to do."""
    # Worded so app.look_first doesn't take a (slow) screenshot: the message text is all she needs.
    return (f"An app just popped up an error box. App: {item['app']} ({item['proc']}), box title: "
            f"'{item['title']}', it says: '{item['text']}'. Explain in plain words what went wrong and how I can fix "
            "it. If it's something you can fix for me, offer to do it.")


def close_hung(item):
    """End the frozen program (like Task Manager's End task) - only the process that owns that window."""
    try:
        p = psutil.Process(item["pid"])
        if p.name().lower() in NEVER_CLOSE:
            return f"I won't close {p.name()} - Windows needs it."
        if not is_hung(item["hwnd"]):
            return f"{item['app']} is responding again, so I left it open. 👍"
        p.kill()
        return f"Closed {item['app']}. You can open it again whenever you like."
    except psutil.NoSuchProcess:
        return f"{item['app']} has already closed."
    except psutil.Error as e:
        return f"I couldn't close {item['app']}: {e}"
