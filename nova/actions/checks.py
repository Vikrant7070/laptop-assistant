"""Postcondition checks for skill steps (skills.py `expect:` lines).

The skill runner evaluates these itself after a step, so Nova never has to take the model's word that something
worked ("you're all set on Crunchyroll" with nothing opened). Each check returns (ok, what_was_seen). A check keeps
trying for a few seconds, because a page that was just opened takes a moment to show up.

    window_open "Crunchyroll"     a window whose title or program contains the text
    front_window "Crunchyroll"    the window in front has the text in its title
    page_has "Log In"             the page / window in front shows the text (also a button or box with that name)
    page_lacks "Log In"           ...does not show it
    fullscreen                    the window in front covers its whole screen
    dark_mode on|off              Windows' app colour mode
    process_running "chrome"      a program with that name is running
    result_has "text"             the step's own output contains the text
"""
import time

NAMES = {"window_open", "front_window", "page_has", "page_lacks", "fullscreen", "dark_mode", "process_running",
         "result_has"}
NEEDS_ARG = NAMES - {"fullscreen"}
INSTANT = {"result_has", "dark_mode"}        # nothing to wait for: the answer is known now


def _tools():
    from nova.actions import tools
    return tools


def _front_hwnd():
    return _tools()._user32.GetForegroundWindow()


def _window_for(ctx):
    """The window a page check looks at: the one the last step named, else the one in front."""
    t = _tools()
    name = (ctx or {}).get("window") or ""
    if name:
        w = t._target_window(name)
        if w:
            return w
    hwnd = _front_hwnd()
    return (hwnd, t._title_of(hwnd), "") if hwnd else None


def window_open(arg, ctx):
    q = arg.lower()
    for w in _tools()._windows():
        if q in str(w[1]).lower() or q in str(w[2]).lower():
            return True, f"window '{w[1]}'"
    return False, "no such window"


def front_window(arg, ctx):
    title = _tools()._title_of(_front_hwnd()) or ""
    return arg.lower() in title.lower(), f"in front: '{title}'"


def _page_has(arg, ctx):
    t = _tools()
    w = _window_for(ctx)
    if not w:
        return False, "no window"
    q = arg.lower()
    try:
        if q in (t.page_text(w[0], 20000) or "").lower():
            return True, f"'{w[1]}' shows it"
    except Exception:
        pass
    try:
        _, items = t._scan(w[1], limit=300)
        if any(q in str(i["name"]).lower() for i in items):
            return True, f"'{w[1]}' has a control called it"
    except Exception:
        pass
    return False, f"'{w[1]}' doesn't show it"


def page_has(arg, ctx):
    return _page_has(arg, ctx)


def page_lacks(arg, ctx):
    ok, seen = _page_has(arg, ctx)
    return (not ok), seen


def fullscreen(arg, ctx):
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    hwnd = _front_hwnd()
    if not hwnd:
        return False, "no window in front"
    rect = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rect))

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]
    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(MonitorInfo)
    mon = u.MonitorFromWindow(hwnd, 2)
    if not mon or not u.GetMonitorInfoW(mon, ctypes.byref(info)):
        return False, "couldn't read the screen size"
    m = info.rcMonitor
    full = rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
    return full, "fills the screen" if full else "doesn't fill the screen"


def dark_mode(arg, ctx):
    import winreg
    want_dark = arg.strip().lower() in ("on", "true", "yes", "dark")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            light = winreg.QueryValueEx(k, "AppsUseLightTheme")[0]
    except OSError:
        return False, "couldn't read the colour mode"
    return (light == 0) == want_dark, "dark" if light == 0 else "light"


def process_running(arg, ctx):
    import psutil
    q = arg.lower().removesuffix(".exe")
    for p in psutil.process_iter(["name"]):
        if q in (p.info.get("name") or "").lower():
            return True, f"{p.info['name']} is running"
    return False, "not running"


def result_has(arg, ctx):
    out = (ctx or {}).get("result", "")
    return arg.lower() in out.lower(), "the step's result"


def evaluate(check, ctx=None, timeout=8.0, interval=0.5):
    """Run one parsed check ({"check": name, "arg": text}), retrying until it passes or `timeout` seconds are up.
    Returns (ok, seen). A check that crashes counts as failed (never as passed)."""
    fn = globals().get(check["check"])
    if check["check"] not in NAMES or not callable(fn):
        return False, f"unknown check {check['check']}"
    end = time.time() + (0 if check["check"] in INSTANT else timeout)
    seen = ""
    while True:
        try:
            ok, seen = fn(check.get("arg", ""), ctx or {})
        except Exception as e:
            ok, seen = False, f"couldn't check ({type(e).__name__}: {e})"
        if ok or time.time() >= end:
            return ok, seen
        time.sleep(interval)
