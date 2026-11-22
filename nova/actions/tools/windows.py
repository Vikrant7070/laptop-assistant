"""Windows and the keyboard: listing, focusing, moving windows, typing (by pasting), and the app guard that keeps keys aimed at the app the user asked for."""


import datetime as dt
import re
import time

import psutil

from nova import platform
from nova.actions.registry import tool
from nova.actions.tools import apps, common, forms



def _windows():
    """Open windows, topmost first: [(hwnd, title, proc), ...] (nova.platform; tests install a FakeDesktop)."""
    return platform.current().windows()


@tool("List open windows and their apps.", friendly="Looked at open windows", core=True, props={}, required=[])
def list_windows():
    return "\n".join(f"{t}  [{p}]" for _, t, p in _windows()) or "No open windows."


def _find_window(title_or_app):
    q = title_or_app.lower().removesuffix(".exe")
    for w in _windows():
        if q in w[1].lower() or q == w[2].lower().removesuffix(".exe"):
            return w
    return None


def _focus(hwnd):
    """Bring a window to the front without pressing keys (nova.platform)."""
    platform.current().focus(hwnd)


# Doing it like a person, so the user can watch: Nova walks to the window's own title-bar button and the real mouse
# clicks it. Found with UI Automation (exact, ~60 ms); apps that draw their own title bar (Chrome, VS Code, Electron)
# don't expose those buttons, so their top-right corner is used - where the X always is. If a click doesn't do it,
# the old way (a window message) finishes the job. Without her 3D body on screen, only the old way is used.
CAPTION_NAMES = {"close": ("close",), "maximize": ("maximize", "restore"), "minimize": ("minimize",)}


CAPTION_SLOT = {"close": 0.5, "maximize": 1.5, "minimize": 2.5}     # standard buttons are ~46 px wide, X rightmost


def _frame(hwnd):
    """The window's visible rectangle (without the invisible resize borders)."""
    return platform.current().frame_rect(hwnd)


def _caption_button(hwnd, which):
    """Screen point of a window's minimize / maximize / close button."""
    left, top, right, bottom = _frame(hwnd)
    try:
        import uiautomation as auto
        names = CAPTION_NAMES[which]
        for ctrl, _ in auto.WalkControl(auto.ControlFromHandle(hwnd), maxDepth=4):
            if ctrl.ControlTypeName == "ButtonControl" and ctrl.Name.lower().strip() in names:
                r = ctrl.BoundingRectangle
                if r.right > r.left and r.top - top < 90:
                    return (r.left + r.right) // 2, (r.top + r.bottom) // 2
    except Exception:
        pass  # intentional: UI Automation/cleanup is best effort; the tool reports its own result
    scale = platform.current().dpi(hwnd) / 96
    return int(right - CAPTION_SLOT[which] * 46 * scale), int(top + 16 * scale)


def _done(hwnd, action):
    d = platform.current()
    if action == "close":
        return not d.is_window(hwnd) or not d.is_visible(hwnd)
    if action == "minimize":
        return d.is_minimized(hwnd)
    return d.is_maximized(hwnd)


def _asks_to_save(hwnd, pid):
    """After clicking X: is the app now showing its own little window ("Save changes?") instead of closing?"""
    fg = platform.current().foreground()
    if not fg or fg == hwnd:
        return False
    return platform.current().window_pid(fg) == pid


def _window_by_hand(hwnd, title, action):
    """Nova walks over and clicks the window's own button -> (done, note)."""
    from nova.avatar import avatar_client
    d = platform.current()
    pid = d.window_pid(hwnd)
    if avatar_client.running():
        if d.is_minimized(hwnd) and action != "minimize":
            d.show(hwnd, 9)                      # bring a minimized window back first, so its X can be clicked
        _focus(hwnd)
        time.sleep(0.35)
        if action == "maximize" and d.is_maximized(hwnd):
            return True, ""
        x, y = _caption_button(hwnd, action)
        label = {"close": "X", "minimize": "minimize", "maximize": "maximize"}[action]
        forms._nova_click(x, y, label=f"{label} on {title[:25]}")
        end = time.time() + 2.0
        while time.time() < end and not _done(hwnd, action):
            time.sleep(0.1)
        if _done(hwnd, action):
            return True, ""
        if action == "close" and _asks_to_save(hwnd, pid):
            return False, "asks"
    # No body on screen, or the click didn't do it: the old way.
    if action == "close":
        d.close(hwnd)                            # WM_CLOSE: the app may still ask to save
    else:
        d.show(hwnd, {"minimize": 6, "maximize": 3}[action])
    return True, ""


@tool("Focus, minimize, maximize or close an open window (match by title or app name).", friendly="Managed a window",
      risky="Control a window", core=True,
      props={"title_or_app": common.S, "action": {"type": "string", "enum": ["focus", "minimize", "maximize", "close"]}},
      required=["title_or_app", "action"])
def window_action(title_or_app, action):
    w = _find_window(title_or_app)
    if not w:
        return f"No open window matches '{title_or_app}'."
    hwnd, title, _ = w
    if action == "focus":
        _focus(hwnd)
        return f"Switched to '{title}'."
    if action not in CAPTION_NAMES:
        return "action must be focus, minimize, maximize or close"
    done, note = _window_by_hand(hwnd, title, action)
    if note == "asks":
        return (f"'{title}' is asking whether to save before it closes. Tell the user and let them choose - "
                "don't click Save or Don't Save yourself.")
    return f"{action.title()}d '{title}'."


def _own_pids():
    """Nova's own process and its children (the pywebview / WebView2 window) - never closed by close_all_windows."""
    me = psutil.Process()
    return {me.pid, *(c.pid for c in me.children(recursive=True))}


SKIP_PROCS = {"textinputhost.exe", "applicationframehost.exe", "shellexperiencehost.exe", "searchhost.exe",
              "startmenuexperiencehost.exe", "lockapp.exe"}      # Windows' own invisible/helper windows


@tool("Close every open app window (not Nova). Apps with unsaved work may ask to save.",
      friendly="Closed all windows", risky="Close all windows",
      hints="close all windows, close everything, close all apps", props={}, required=[])
def close_all_windows():
    """Close every open app window except Nova's own and the Windows shell - one by one, Nova walking to each X
    (the top window first). Stops at an app that asks whether to save: that choice is the user's."""
    mine, closed, d = _own_pids(), 0, platform.current()
    for hwnd, title, proc in _windows():
        if d.window_pid(hwnd) in mine or proc.lower() in SKIP_PROCS or not d.is_window(hwnd):
            continue
        if proc.lower() == "explorer.exe":       # only File Explorer folder windows, never the taskbar/desktop
            if d.class_name(hwnd) != "CabinetWClass":
                continue
        done, note = _window_by_hand(hwnd, title, "close")
        if note == "asks":
            return (f"Closed {closed} window{'s' if closed != 1 else ''}, then '{title}' asked whether to save. "
                    "Tell the user and let them choose; the rest are still open.")
        closed += 1
    return f"Closed {closed} window{'s' if closed != 1 else ''}." if closed else "No windows were open."


@tool("Show the desktop.", friendly="Showed the desktop", props={}, required=[])
def minimize_all_windows():
    platform.current().press("windows+d")
    return "Showing the desktop."


def _point_at_focus(label):
    """Nova walks over and points at the box that has the keyboard focus (where the text will go). Returns True
    when she is pointing (call _stop_pointing after)."""
    from nova.avatar import avatar_client
    if not avatar_client.running():
        return False
    try:
        import uiautomation as auto
        r = auto.GetFocusedControl().BoundingRectangle
        if r.right <= r.left:
            return False
        x, y = min(r.left + 40, (r.left + r.right) // 2), (r.top + r.bottom) // 2
    except Exception:
        return False
    avatar_client.send("say", text=label[:60], secs=3)
    avatar_client.send("go_point", x=x, y=y, timeout=15)
    avatar_client.send("clickthrough", on=True)     # the keys must reach the box, not her
    return True


def _stop_pointing():
    from nova.avatar import avatar_client
    avatar_client.send("clickthrough", on=False)
    avatar_client.send("finish_point")


def _clip_save():
    """A copy of everything on the clipboard (nova.platform): [(format, bytes)], [] when empty, None if unreadable."""
    return platform.current().clipboard_save()


def _clip_set_text(text):
    """Put text on the clipboard, out of clipboard history (nova.platform). -> its sequence number, 0 if busy."""
    return platform.current().clipboard_set_text(text)


def _clip_restore(saved, our_seq):
    """Put the user's clipboard back unless something else was copied since (nova.platform)."""
    platform.current().clipboard_restore(saved, our_seq)


def _focused_text():
    """What the focused box holds (to see a paste land), or None when it can't be read (nova.platform)."""
    return platform.current().focused_text()


def _wait_for_paste(before, timeout=2.0):
    """Ctrl+V was pressed: wait until the app has read the clipboard (its text changed) before it is put back.
    If the box can't be read, wait a fixed half second."""
    time.sleep(0.15)
    if before is None:
        time.sleep(0.35)
        return
    end = time.time() + timeout
    while time.time() < end:
        now = _focused_text()
        if now is None or now != before:
            time.sleep(0.05)
            return
        time.sleep(0.05)


def _type_into_focus(text):
    """Put text into whatever has the keyboard focus. Returns how it went in: "paste" or "keys"."""
    if not text:
        return "paste"
    saved = _clip_save()
    before = _focused_text()
    seq = _clip_set_text(text) if saved is not None else 0
    if not seq:
        for ch in text:                 # clipboard busy: one letter at a time, slow enough to keep up
            platform.current().write(ch)
            time.sleep(0.03)
        return "keys"
    try:
        platform.current().press("ctrl+v")
        _wait_for_paste(before)
    finally:
        _clip_restore(saved, seq)
    return "paste"


@tool("Type text into an app that is already open; into_window = that app's name, e.g. 'notepad' "
      "(it is brought to the front first). If the app isn't in the open windows list, call open_app first.",
      friendly="Typed text", risky="Type on your keyboard", core=True, props={"text": common.S, "into_window": common.S},
      required=["text"])
def type_text(text, into_window=None):
    if into_window:
        msg = window_action(into_window, "focus")
        if msg.startswith("No open"):
            return msg
        time.sleep(0.4)
    if forms._focused_is_password():
        return forms.NO_SECRETS
    pointing = _point_at_focus("Typing…")
    try:
        _type_into_focus(text)
    finally:
        if pointing:
            _stop_pointing()
    return f"Typed {len(text)} characters."


@tool("Press a key or shortcut in the window in front, e.g. 'ctrl+s', 'enter', 'windows+e'. Never "
      "use it to open or switch apps (alt+tab, ctrl+n): use open_app or window_action focus.",
      friendly="Pressed keys", risky="Press keys", core=True, props={"keys": common.S}, required=["keys"])
def press_keys(keys):
    platform.current().press(keys)
    return f"Pressed {keys}."


# 2026-11-20 "type hello judge in notepad" with Notepad closed: the small brain pressed alt+tab, ctrl+shift+n and
# ctrl+n in VS Code (the window in front), then typed into "Untitled-1 - Visual Studio Code". Now, when a request
# names an app, keys that open or switch windows only go to that app, and typing only goes into that app's window.
# If the app isn't open she is told to open_app it first.
_NAMED_APP_RE = re.compile(
    r"\b(?:in|into|inside|using|open|launch|start)\s+(?:up\s+)?(?:the\s+|my\s+|a\s+(?:new\s+)?|new\s+)?("
    + "|".join(re.escape(k) for k in sorted(apps.APPS, key=len, reverse=True)) + r")\b", re.I)


APP_PROCS = {   # process names (no .exe) an app's windows can have, beyond its own name and APPS target
    "calculator": {"calculatorapp"}, "calc": {"calculatorapp"}, "terminal": {"windowsterminal"},
    "cmd": {"windowsterminal", "conhost"}, "powershell": {"windowsterminal", "conhost"},
    "outlook": {"olk"}, "mail": {"olk", "hxoutlook"}, "settings": {"systemsettings"}, "camera": {"windowscamera"},
    "store": {"winstore.app"}, "clock": {"time"}, "photos": {"photos"}, "paint": {"mspaint"},
}


SWITCH_KEYS = {"alt+tab", "alt+shift+tab", "ctrl+alt+tab", "windows+tab"}   # these leave the window in front


NEW_WINDOW_KEYS = {"ctrl+n", "ctrl+shift+n", "ctrl+t", "ctrl+shift+t"}      # these make a new window/tab in the front app


def named_app(request):
    """The app a request names ("type hello in notepad", "open chrome and ..."), as an APPS key, or ""."""
    m = _NAMED_APP_RE.search(request or "")
    return m.group(1).lower() if m else ""


def _keys_combo(keys):
    """'Shift + Ctrl+N' -> 'ctrl+shift+n' (modifiers in one order), to compare shortcuts."""
    parts = [p.strip().lower() for p in str(keys or "").replace(" ", "").split("+") if p.strip()]
    names = {"control": "ctrl", "win": "windows", "cmd": "windows"}
    parts = [names.get(p, p) for p in parts]
    order = ["ctrl", "alt", "shift", "windows"]
    mods = sorted({p for p in parts if p in order}, key=order.index)
    rest = [p for p in parts if p not in order]
    return "+".join(mods + rest)


def window_is_app(w, app):
    """Is this window (hwnd, title, process) one of the app's own windows?"""
    _, title, proc = w
    target = apps.APPS.get(app, app).lower().rstrip(":").removesuffix(".exe")
    procs = {app.replace(" ", ""), target} | APP_PROCS.get(app, set())
    if proc.lower().removesuffix(".exe") in procs:
        return True
    last = title.lower().replace("​", "").rsplit(" - ", 1)[-1].strip()   # "Untitled - Notepad" -> "notepad"
    return app in last


def _app_window(app, wait=0.0):
    """An open window of the app (waits up to `wait` seconds for one to appear - right after open_app)."""
    end = time.time() + wait
    while True:
        for w in _windows():
            if window_is_app(w, app):
                return w
        if time.time() >= end:
            return None
        time.sleep(0.25)


def app_guard(tool, args, app, wait=0.0):
    """Before press_keys / type_text, when the request named an app: None if it's fine, else why it wasn't done."""
    if not app or tool not in ("press_keys", "type_text"):
        return None
    args = args or {}
    if tool == "press_keys":
        keys = _keys_combo(args.get("keys"))
        if keys not in SWITCH_KEYS | NEW_WINDOW_KEYS:
            return None
        front = forms._target_window(None)
        if keys in NEW_WINDOW_KEYS and front and window_is_app(front, app):
            return None                                     # a new tab/document inside the app itself is fine
        where = f"'{front[1][:60]}'" if front else "the window in front"
    else:
        into = str(args.get("into_window") or "").strip()
        target = _find_window(into) if into else forms._target_window(None)
        if target and window_is_app(target, app):
            return None
        where = f"'{target[1][:60]}'" if target else f"'{into}'" if into else "the window in front"
    did = "pressed" if tool == "press_keys" else "typed"
    mine = _app_window(app, wait)
    if not mine:
        return (f"Not done - nothing {did}: {app} isn't open, and this would have gone into {where}. "
                f"Call open_app(name='{app}') first, then type_text(text=..., into_window='{app}').")
    return (f"Not done - nothing {did}: this would have gone into {where}, not {app}. "
            f"Use type_text(text=..., into_window='{app}') - it brings '{mine[1][:60]}' to the front first.")


@tool("Read the clipboard text.", friendly="Read the clipboard", props={}, required=[])
def get_clipboard():
    return platform.current().clipboard_text()[:4000] or "(clipboard is empty)"


SELECTION_MAX = 6000    # about a page and a half - longer is better copied into a file


@tool("Get the text the user has highlighted/selected in their app (copies it for you, "
      "then restores their clipboard). Use it for 'read this', 'what does the selected text say', "
      "'summarise/translate what I selected'. When asked to read it, say it word for word.",
      friendly="Read the selected text",
      hints="read this selected highlighted text out loud what does it say read it to me", props={}, required=[])
def read_selection():
    """The text highlighted in the user's app: copy it (Ctrl+C in that window), then put their clipboard back.
    If Nova's own chat window is in front, the app behind it is brought forward first - it keeps its selection."""
    old = platform.current().clipboard_text()
    w = forms._target_window(None)
    if not w:
        return "Error in read_selection: no app window is open to read from."
    hwnd, title, _ = w
    marker = f"__nova_selection_{time.time()}__"
    platform.current().set_clipboard_text(marker)                       # so an empty selection is told apart from old clipboard text
    try:
        if platform.current().foreground() != hwnd:
            _focus(hwnd)
            time.sleep(0.25)
        platform.current().press("ctrl+c")
        text, end = marker, time.time() + 1.5
        while text == marker and time.time() < end:   # some apps take a moment to fill the clipboard
            time.sleep(0.05)
            text = platform.current().clipboard_text()
    finally:
        platform.current().set_clipboard_text(old)
    if text == marker or not text.strip():
        return f"Nothing is selected in '{title[:60]}'. Highlight some text first, then ask again."
    text = text.strip()
    cut = len(text) > SELECTION_MAX
    return text[:SELECTION_MAX] + ("\n[... cut here: the selection was longer]" if cut else "")


@tool("Put text on the clipboard.", friendly="Copied to clipboard", props={"text": common.S}, required=["text"])
def set_clipboard(text):
    platform.current().set_clipboard_text(text)
    return "Copied to the clipboard."


@tool("Save a screenshot to Pictures/Nova Screenshots.", friendly="Took a screenshot",
      hints="screenshot capture the screen", props={}, required=[])
def take_screenshot():
    from PIL import ImageGrab
    folder = common.HOME / "Pictures" / "Nova Screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screenshot_{dt.datetime.now():%Y-%m-%d_%H-%M-%S}.png"
    ImageGrab.grab(all_screens=True).save(path)
    return f"Saved screenshot to {path}"
