"""👀 "Watch me": Nova learns a skill by watching the user do it (and fixes a step when the user shows her).

    record  - between "watch me" and "done": every click (what was clicked, by its name - not where), every key
              combination, what ended up in each text box, the page each browser window is on, and which app is in
              front. events_to_trace() turns that into the same kind of trace a task leaves behind, so the usual
              path makes it a draft skill (skills.steps_from_trace / draft_from_steps).
    listen  - while a skill runs: only notes THAT the user clicked or typed (nothing about what), so the skill can
              stop and ask "you took over - want to show me how this step should go?" (app.run_skill).

How it works: two Windows low-level hooks (mouse and keyboard) via ctypes - no extra package, nothing injected into
other programs. The hook callbacks only put a tiny tuple on a queue and return at once: a slow hook makes the whole
mouse lag, and Windows drops a hook that is too slow. A worker thread does the slow part (UI Automation: which
button, which box, what's in it). Hooks run only while recording or listening, never otherwise.

Privacy, by design:
  * Nothing is recorded unless the user said "watch me" (or a skill is running, and then only "the user acted").
  * Input Windows marks as injected (Nova's own clicks and typing) is ignored - only the user's hands count.
  * Keystrokes are never stored. What a box holds is read once, when the user leaves it - and never for a password
    box, a code/PIN box, or a value that looks like a secret: a sign-in becomes one `login_if_needed` step.
  * Clicks and typing in Nova's own windows are skipped. The recording is kept in memory only (never on disk), and
    only the cleaned-up steps go anywhere (see skill_hints in app.py).
"""
import ctypes
import os
import queue
import re
import threading
import time
from ctypes import wintypes

MAX_EVENTS = 300
MAX_SECS = 600
ENABLED = True                 # tests switch the real hooks off (conftest)
busy = lambda: False           # app.py: True while Nova herself is working - the windows she opens are not the user's steps

BROWSERS = {"chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe", "vivaldi.exe"}
SHELL = {"searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe", "textinputhost.exe",
         "searchapp.exe", "lockapp.exe"}
# explorer.exe is both the taskbar / desktop (left out) and File Explorer windows (kept): the window class tells them apart
SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW", "NotifyIconOverflowWindow",
                 "TopLevelWindowForOverflowXamlIsland", "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow"}
NOVA_TITLES = {"nova", "nova 3d", "nova ear", "tk"}
BROWSER_NAMES = re.compile(r"^(google chrome|microsoft​? ?edge|brave|mozilla firefox|firefox|opera|vivaldi)$", re.I)
ADDRESS_RE = re.compile(r"address and search bar|search or enter address|address bar|search with .* or enter address", re.I)
EDITABLE = {"Edit", "Document", "ComboBox"}
CLICKY = {"Button", "Hyperlink", "MenuItem", "TabItem", "ListItem", "CheckBox", "RadioButton", "TreeItem", "SplitButton",
          "ComboBox", "Edit", "DataItem"}
ENTER_LIKE = {"enter", "tab", "esc"}
CURSOR_KEYS = {"left", "right", "up", "down", "home", "end"}
HOTKEYS = {"ctrl+alt+w", "ctrl+alt+n"}            # Nova's own shortcuts are never part of a skill


# ------------------------------------------------------------------ pure helpers (tested)

def app_name(title, proc=""):
    """A short, stable name for a window that _find_window can match: 'lofi radio - YouTube - Google Chrome' ->
    'YouTube', 'notes.txt - Notepad' -> 'Notepad'. (The site/app part, not the page or file - it stays the same next
    time, and it keeps e-mail addresses and file names in titles out of the skill.)"""
    parts = [p.strip() for p in re.split(r"\s+[-—–|]\s+", str(title or "")) if p.strip()]
    parts = [re.sub(r"^\(\d+\)\s*", "", p) for p in parts]
    if len(parts) > 1 and BROWSER_NAMES.match(parts[-1]):
        parts = parts[:-1]
    name = parts[-1] if parts else ""
    if not name:
        name = re.sub(r"\.exe$", "", str(proc or ""), flags=re.I)
    return name[:40]


def _T(tool, t=0.0, **args):
    return {"tool": tool, "args": args, "ok": True, "out": "", "t": t}


def _good_url(u):
    u = str(u or "").strip()
    return bool(u) and not re.match(r"^(chrome|edge|about|brave|opera|vivaldi)://|^about:|^chrome-extension:", u, re.I) \
        and " " not in u.strip()


def events_to_trace(events):
    """Recorded events -> trace entries ({"tool", "args", "ok"}), the same shape a task leaves behind, so
    skills.steps_from_trace can clean them up. The rules, in short:
      * a link clicked in a browser, followed by the page changing, becomes open_website(<new page>) - direct and
        sturdy; typing in the address bar and pressing Enter does too;
      * a click on something with a name becomes click_element(name, window); without a name it can't be replayed,
        so it becomes a small brain step (click_at -> brain in steps_from_trace);
      * clicking into a box and typing becomes one fill_in(text, box, window) (press_enter if Enter followed);
      * a password / sign-in becomes login_if_needed; a code box wait_for_code - never the value;
      * a new app coming to the front becomes open_app; switching back to an open one window_action focus
        (left out when the very next step names that window anyway);
      * clicks in the taskbar / Start menu and anything in Nova's own windows are left out."""
    ev = [e for e in events if not e.get("nova")]
    out, used, nav = [], set(), {}

    def win(e):
        return app_name(e.get("window", ""), e.get("proc", ""))

    def shell(e):
        return bool(e.get("shell")) or str(e.get("proc", "")).lower() in SHELL

    def after(i, kinds, secs, same=True):
        for j in range(i + 1, len(ev)):
            f = ev[j]
            if f["t"] - ev[i]["t"] > secs:
                return None
            if f["kind"] in kinds and (not same or win(f) == win(ev[i])) and j not in used:
                return j
        return None

    def next_action(i):
        for j in range(i + 1, len(ev)):
            if ev[j]["kind"] != "focus":
                return j
        return None

    for i, e in enumerate(ev):
        if i in used:
            continue
        k, w = e["kind"], win(e)
        if k == "focus":
            if shell(e) or not w:
                continue
            if e.get("new_app"):
                out.append(_T("open_app", e["t"], name=w))
                continue
            if i + 1 < len(ev) and ev[i + 1]["kind"] == "focus":
                continue                                 # passing through: only the last switch matters
            if i > 0 and ev[i - 1]["kind"] == "click" and win(ev[i - 1]) == w and e["t"] - ev[i - 1]["t"] < 2:
                continue                                 # the click just before brought it to the front
            j = next_action(i)
            if j is not None and win(ev[j]) == w and ev[j]["t"] - e["t"] < 3:
                continue                                 # the next step names this window itself
            out.append(_T("window_action", e["t"], title_or_app=w, action="focus"))
        elif k == "click":
            if shell(e):
                continue
            if e.get("address_bar"):
                nav[w] = True
                continue
            if e.get("password"):
                continue                                 # the sign-in step comes from what happens next
            if e.get("browser") and e.get("ctype") == "Hyperlink":
                j = after(i, {"url"}, 4)
                if j is not None and _good_url(ev[j].get("url")):
                    used.add(j)
                    out.append(_T("open_website", e["t"], url_or_search=ev[j]["url"]))
                    continue
            j = next_action(i)
            if e.get("ctype") in EDITABLE and j is not None and ev[j]["kind"] in ("typed", "login", "code") \
                    and win(ev[j]) == w:
                continue                                 # fill_in clicks the box itself
            if not e.get("name"):
                out.append(_T("click_at", e["t"], x=e.get("x", 0), y=e.get("y", 0)))
                continue
            args = {"name": e["name"], "window": w}
            if e.get("button") == "right":
                args["button"] = "right"
            if e.get("double"):
                args["double"] = True
            out.append(_T("click_element", e["t"], **args))
        elif k == "typed":
            if e.get("address_bar"):
                nav[w] = True
                continue
            if e.get("secret"):
                continue                                 # a value that looked like a secret: left out entirely
            val, box = e.get("value"), e.get("box", "")
            j = after(i, {"key"}, 3)
            enter = j is not None and ev[j].get("combo") == "enter"
            if enter:
                used.add(j)
            if val is None or e.get("ctype") not in ("Edit", "ComboBox"):
                if e.get("ctype") == "Document" and val and len(val) <= 200:
                    out.append(_T("type_text", e["t"], text=val, into_window=w))
                else:
                    out.append(_T("_brain", e["t"], instr=f"Type into '{box or 'the text box'}' in {w} what this task "
                                  "needs (the user typed something there when showing me)",
                                  tools=["list_clickable", "click_element", "fill_in", "type_text"]))
                continue
            args = {"text": val, "box": "" if box in ("", "(text box)") else box, "window": w}
            args["press_enter"] = enter
            out.append(_T("fill_in", e["t"], **args))
        elif k == "login":
            if out and out[-1]["tool"] == "login_if_needed" and out[-1]["args"].get("window") == w:
                continue
            j = after(i, {"key"}, 3)
            if j is not None and ev[j].get("combo") == "enter":
                used.add(j)                              # the Enter that sent the password is part of signing in
            out.append(_T("login_if_needed", e["t"], window=w))
        elif k == "code":
            j = after(i, {"key"}, 3)
            if j is not None and ev[j].get("combo") == "enter":
                used.add(j)
            out.append(_T("wait_for_code", e["t"], window=w))
        elif k == "key":
            if e.get("address_bar") or shell(e):
                if e.get("address_bar"):
                    nav[w] = True
                continue
            combo = e.get("combo", "")
            if not combo or combo in HOTKEYS:
                continue
            out.append(_T("press_keys", e["t"], keys=combo))
        elif k == "url":
            if not _good_url(e.get("url")):
                continue
            caused = any(ev[j]["kind"] in ("click", "key", "url", "typed", "login") and win(ev[j]) == w
                         and e["t"] - ev[j]["t"] < 4 for j in range(max(0, i - 8), i))
            if nav.pop(w, False) or not caused:
                out.append(_T("open_website", e["t"], url_or_search=e["url"]))
            # else: the page changed because of the click / key just before - that step is the one to repeat
    return out


# ------------------------------------------------------------------ live capture (Windows only)

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_QUIT = 0x201, 0x204, 0x12
LLKHF_INJECTED, LLKHF_LOWER_IL_INJECTED, LLMHF_INJECTED, LLMHF_LOWER_IL_INJECTED = 0x10, 0x02, 0x01, 0x02
MODS = {0x10: "shift", 0xA0: "shift", 0xA1: "shift", 0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl", 0x12: "alt", 0xA4: "alt",
        0xA5: "alt", 0x5B: "windows", 0x5C: "windows"}
NAMED = {0x0D: "enter", 0x09: "tab", 0x1B: "esc", 0x20: "space", 0x08: "backspace", 0x2E: "delete", 0x25: "left",
         0x26: "up", 0x27: "right", 0x28: "down", 0x24: "home", 0x23: "end", 0x21: "page up", 0x22: "page down",
         0x2D: "insert", 0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`", 0xDB: "[",
         0xDC: "\\", 0xDD: "]", 0xDE: "'"}


def key_name(vk):
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk).lower()
    if 0x60 <= vk <= 0x69:
        return str(vk - 0x60)
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x6F}"
    return NAMED.get(vk)


def printable(vk):
    return 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39 or 0x60 <= vk <= 0x6F or vk == 0x20 or 0xBA <= vk <= 0xE2


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_u = _k = None


def _dlls():
    """Our own copies of user32/kernel32, so the argtypes set here never change how tools.py's calls behave."""
    global _u, _k
    if _u is None:
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        u.SetWindowsHookExW.restype = wintypes.HHOOK
        u.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        u.CallNextHookEx.restype = LRESULT
        u.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        u.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.WindowFromPoint.restype = wintypes.HWND
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        k.GetModuleHandleW.restype = wintypes.HMODULE
        _u, _k = u, k
    return _u, _k


def _window_info(hwnd):
    import psutil
    u, _ = _dlls()
    if not hwnd:
        return "", "", 0
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        proc = psutil.Process(pid.value).name()
    except Exception:
        proc = ""
    return buf.value, proc, pid.value


def _is_shell(hwnd, proc):
    """The taskbar, Start menu or desktop (not a real app window)."""
    if str(proc).lower() in SHELL:
        return True
    if str(proc).lower() != "explorer.exe":
        return False
    u, _ = _dlls()
    buf = ctypes.create_unicode_buffer(128)
    u.GetClassNameW(hwnd, buf, 128)
    return buf.value in SHELL_CLASSES


def _is_nova(title, proc, pid):
    return str(title).strip().lower() in NOVA_TITLES or pid == os.getpid() or (
        str(proc).lower() in ("pythonw.exe", "python.exe") and "nova" in str(title).lower())


class Capture:
    def __init__(self):
        self.q = queue.SimpleQueue()
        self.mode = None                  # None | "record" | "listen"
        self.events = []
        self.goal = ""
        self.started = 0.0
        self.last_user = 0.0              # listen mode: when the user last clicked / typed (outside Nova)
        self.limit_hit = False
        self._hook_tid = 0
        self._threads = []
        self._running = False
        self._lock = threading.Lock()

    # ---- the hooks: as little as possible, never raise, always pass the event on
    def _on_mouse(self, n, wp, lp):
        try:
            if n >= 0 and wp in (WM_LBUTTONDOWN, WM_RBUTTONDOWN):
                m = ctypes.cast(lp, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not (m.flags & (LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED)):
                    self.q.put(("mouse", wp, m.pt.x, m.pt.y, time.time()))
        except Exception:
            pass
        return _u.CallNextHookEx(None, n, wp, lp)

    def _on_key(self, n, wp, lp):
        try:
            if n >= 0:
                k = ctypes.cast(lp, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not (k.flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED)):
                    self.q.put(("key", wp, k.vkCode, time.time()))
        except Exception:
            pass
        return _u.CallNextHookEx(None, n, wp, lp)

    def _hook_loop(self, ready):
        u, k = _dlls()
        self._hook_tid = k.GetCurrentThreadId()
        mod = k.GetModuleHandleW(None)
        self._mproc, self._kproc = HOOKPROC(self._on_mouse), HOOKPROC(self._on_key)   # kept alive while hooked
        hm = u.SetWindowsHookExW(WH_MOUSE_LL, self._mproc, mod, 0)
        hk = u.SetWindowsHookExW(WH_KEYBOARD_LL, self._kproc, mod, 0)
        ready["ok"] = bool(hm and hk)
        ready["ev"].set()
        msg = wintypes.MSG()
        try:
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            if hm:
                u.UnhookWindowsHookEx(hm)
            if hk:
                u.UnhookWindowsHookEx(hk)

    # ---- start / stop
    def start(self, mode, goal=""):
        with self._lock:
            if self.mode == mode:
                return ""
            if self.mode:                          # listen -> record (or back): the hooks stay, the worker changes
                self._stop_threads()
            self.mode, self.goal, self.events, self.started, self.limit_hit = mode, goal, [], time.time(), False
            self.last_user = 0.0
            self.q = queue.SimpleQueue()
            self._running = True                   # set before the worker starts, so a quick stop() can't be missed
            ready = {"ev": threading.Event(), "ok": False}
            hook = threading.Thread(target=self._hook_loop, args=(ready,), daemon=True, name="nova-recorder-hooks")
            hook.start()
            ready["ev"].wait(3)
            if not ready["ok"]:
                self.mode = None
                self._stop_threads()
                return "Windows didn't let me watch the mouse and keyboard just now."
            work = threading.Thread(target=self._work, daemon=True, name="nova-recorder-worker")
            work.start()
            self._threads = [hook, work]
            return ""

    def stop(self):
        with self._lock:
            mode, self.mode = self.mode, None
            self._stop_threads()
            return list(self.events) if mode == "record" else []

    def _stop_threads(self):
        u, _ = _dlls()
        if self._hook_tid:
            u.PostThreadMessageW(self._hook_tid, WM_QUIT, 0, 0)
        self._running = False
        for t in self._threads:
            t.join(timeout=3)
        self._threads, self._hook_tid = [], 0

    # ---- the worker: everything slow happens here
    def _work(self):
        if self.mode == "listen":
            self._listen()
            return
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            self._record(auto)

    def _listen(self):
        u, _ = _dlls()
        while self._running and self.mode == "listen":
            try:
                item = self.q.get(timeout=0.3)
            except queue.Empty:
                continue
            if item[0] == "mouse":
                hwnd = u.GetAncestor(u.WindowFromPoint(wintypes.POINT(item[2], item[3])), 2)
            elif item[1] in (WM_KEYDOWN, WM_SYSKEYDOWN) and item[2] not in MODS:
                hwnd = u.GetForegroundWindow()
            else:
                continue
            title, proc, pid = _window_info(hwnd)
            if not _is_nova(title, proc, pid):
                self.last_user = item[-1]

    def _record(self, auto):
        from nova.actions import tools
        u, _ = _dlls()
        try:
            start_procs = {w[2].lower() for w in tools._windows()}
        except Exception:
            start_procs = set()
        seen, mods, seg = set(start_procs), set(), None
        last_fg, last_url, last_poll, last_click = None, {}, 0.0, None

        def emit(e):
            if self.limit_hit:
                return
            self.events.append(e)
            if len(self.events) >= MAX_EVENTS or time.time() - self.started > MAX_SECS:
                self.limit_hit = True

        def end_typing():
            nonlocal seg
            if not seg:
                return
            s, seg = seg, None
            emit(self._typed_event(s))

        while self._running and self.mode == "record":
            try:
                item = self.q.get(timeout=0.25)
            except queue.Empty:
                item = None
            now = time.time()
            if item and item[0] == "mouse":
                _, wp, x, y, t = item
                hwnd = u.GetAncestor(u.WindowFromPoint(wintypes.POINT(x, y)), 2)
                title, proc, pid = _window_info(hwnd)
                if _is_nova(title, proc, pid):
                    continue
                e = self._click_event(auto, x, y, wp, t, title, proc)
                e["shell"] = _is_shell(hwnd, proc)
                if seg and not (e.get("ctype") in EDITABLE and e.get("name") == seg["box"]):
                    end_typing()
                if last_click and t - last_click["t"] < 0.45 and abs(x - last_click["x"]) < 6 and abs(y - last_click["y"]) < 6:
                    last_click["double"] = True              # the second half of a double-click
                    continue
                emit(e)
                last_click = e
            elif item and item[0] == "key":
                _, wp, vk, t = item
                down = wp in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if vk in MODS:
                    (mods.add if down else mods.discard)(MODS[vk])
                    continue
                if not down:
                    continue
                name = key_name(vk)
                if not name:
                    continue
                held = [m for m in ("ctrl", "alt", "shift", "windows") if m in mods]
                chord = [m for m in held if m != "shift"]
                title, proc, pid = _window_info(u.GetForegroundWindow())
                if _is_nova(title, proc, pid):
                    continue
                combo = "+".join(held + [name]) if chord or (held and not printable(vk)) else name   # shift+tab
                if seg and (combo in ("ctrl+v", "ctrl+x", "ctrl+a", "ctrl+z", "ctrl+backspace") or not chord
                            and (printable(vk) or name in ("backspace", "delete") or name in CURSOR_KEYS)):
                    seg["n"] += 1                              # still typing in the same box
                    continue
                if not chord and (printable(vk) or name in ("backspace", "delete")) or combo in ("ctrl+v",):
                    box = self._editable_focus(auto)
                    if box:
                        seg = dict(box, n=1, t=t, window=title, proc=proc)
                        continue
                    if name == "backspace":
                        continue
                was_address = bool(seg and seg.get("address_bar"))
                end_typing()
                if combo in HOTKEYS:
                    continue
                emit({"kind": "key", "combo": combo, "t": t, "window": title, "proc": proc,
                      "address_bar": was_address and name == "enter"})
            # every half second: which window is in front, and which page each browser shows
            if now - last_poll >= 0.5:
                last_poll = now
                fg = u.GetForegroundWindow()
                if fg != last_fg:
                    title, proc, pid = _window_info(fg)
                    last_fg = fg
                    if title and not _is_nova(title, proc, pid) and not busy():
                        if seg:
                            end_typing()
                        emit({"kind": "focus", "t": now, "window": title, "proc": proc, "shell": _is_shell(fg, proc),
                              "new_app": proc.lower() not in seen})
                        seen.add(proc.lower())
                title, proc, pid = _window_info(fg)
                if proc.lower() in BROWSERS and not (seg and seg.get("address_bar")):
                    url = self._browser_url(auto, fg)
                    if url and url != last_url.get(fg):
                        last_url[fg] = url
                        if not busy():
                            emit({"kind": "url", "t": now, "url": url, "window": title, "proc": proc})
        if seg:
            end_typing()

    # ---- UI Automation readers (worker thread only)
    def _click_event(self, auto, x, y, wp, t, title, proc):
        e = {"kind": "click", "t": t, "x": x, "y": y, "button": "right" if wp == WM_RBUTTONDOWN else "left",
             "window": title, "proc": proc, "browser": proc.lower() in BROWSERS, "name": "", "ctype": ""}
        try:
            c = auto.ControlFromPoint(x, y)
            for _ in range(4):                     # the text inside a button is not the button: climb to it
                if not c:
                    break
                ct = c.ControlTypeName.replace("Control", "")
                if ct in CLICKY:
                    break
                p = c.GetParentControl()
                if not p or p.ControlTypeName.replace("Control", "") in ("Window", "Pane", "Document"):
                    break
                c = p
            if c:
                e["ctype"] = c.ControlTypeName.replace("Control", "")
                e["name"] = (c.Name or "").strip()[:80]
                e["password"] = bool(getattr(c, "IsPassword", False))
                e["address_bar"] = e["ctype"] == "Edit" and bool(ADDRESS_RE.search(e["name"]))
        except Exception:
            pass
        return e

    def _editable_focus(self, auto):
        """The box the user is typing in, or None (keys pressed outside a box are shortcuts: 'f' for fullscreen)."""
        try:
            c = auto.GetFocusedControl()
            ct = c.ControlTypeName.replace("Control", "")
            if ct not in EDITABLE:
                return None
            password = bool(getattr(c, "IsPassword", False))
            if not password:
                vp = c.GetValuePattern()
                if vp.IsReadOnly:
                    return None                    # a web page itself, read-only text...
            name = (c.Name or "").strip()[:80]
            return {"ctrl": c, "box": name, "ctype": ct, "password": password, "address_bar": bool(ADDRESS_RE.search(name))}
        except Exception:
            return None

    def _typed_event(self, s):
        """What the user left in a box - or, for anything secret, only that a sign-in / code happened."""
        from nova.actions import forms
        from nova.actions.knowledge import SECRET
        base = {"t": s["t"], "window": s["window"], "proc": s["proc"], "box": s["box"], "ctype": s["ctype"],
                "address_bar": s["address_bar"]}
        if s["password"] or forms.SECRET_BOX.search(s["box"]) and not forms.NOT_CODE.search(s["box"]):
            return {**base, "kind": "code" if forms.CODE_BOX.search(s["box"]) and not s["password"] else "login"}
        if s["address_bar"]:
            return {**base, "kind": "typed", "value": None}
        try:
            val = (s["ctrl"].GetValuePattern().Value or "")[:500]
        except Exception:
            val = None
        if val is not None and SECRET.search(val):
            return {**base, "kind": "typed", "value": None, "secret": True}
        return {**base, "kind": "typed", "value": val}

    def _browser_url(self, auto, hwnd):
        try:
            root = auto.ControlFromHandle(hwnd)
            bar = root.EditControl(searchDepth=14, RegexName="(?i).*(" + ADDRESS_RE.pattern + ")")
            if not bar.Exists(0, 0) or bar.HasKeyboardFocus:
                return ""
            return (bar.GetValuePattern().Value or "").strip()
        except Exception:
            return ""


_cap = Capture()


def start(goal="", mode="record"):
    """Start watching ("record") or only noticing that the user acts ("listen"). Returns "" or why it can't."""
    if not ENABLED:
        return "Watching isn't available here."
    if mode == "listen" and _cap.mode == "record":
        return ""                                  # already recording: that sees more than listening would
    return _cap.start(mode, goal)


def stop():
    """Stop. Returns the recorded events (empty after listening)."""
    if not ENABLED:
        return []
    return _cap.stop()


def stop_listening():
    if ENABLED and _cap.mode == "listen":
        _cap.stop()


def recording():
    return ENABLED and _cap.mode == "record"


def listening():
    return ENABLED and _cap.mode == "listen"


def goal():
    return _cap.goal


def limit_hit():
    return _cap.limit_hit


def user_acted_since(t):
    """Listen mode: did the user click or type (outside Nova's windows) after time t?"""
    return ENABLED and _cap.mode == "listen" and _cap.last_user > t
