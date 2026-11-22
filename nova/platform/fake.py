"""A pretend desktop for tests: windows in z-order, a front window, and a log of what Nova did to them."""


class FakeDesktop:
    """FakeDesktop([(hwnd, title, proc), ...], front=hwnd). The list is topmost first; front defaults to the first.

    `log` records actions as tuples, e.g. ("focus", 2), so a test can check what Nova did without patching her
    private helpers.
    """

    def __init__(self, windows=(), front=None, clipboard="", focused_text=None):
        self.scene = []
        self.log = []
        self.clipboard = clipboard          # plain text, or [(format, bytes)] for a "rich" clipboard
        self.clip_seq = 0
        self.text_in_focus = focused_text   # what the focused box holds; None = can't be read
        # one window's state, filled in by a test: alive = handles that exist without being in the scene (a hidden
        # or parked window, a dialog); kids = {hwnd: [(child, class, title, visible)]}
        self.minimized, self.maximized, self.hung, self.alive = set(), set(), set(), set()
        self.pids, self.classes, self.kids, self.virtual = {}, {}, {}, (0, 1920)
        self.set_windows(windows, front)

    def logged(self, kind):
        """What was logged of one kind, e.g. logged("press") -> ["ctrl+v", "enter"]."""
        return [entry[1] if len(entry) == 2 else entry[1:] for entry in self.log if entry[0] == kind]

    # -- setting the scene
    def set_windows(self, windows, front=None):
        from nova.platform import Window
        self.scene = [Window(*w) for w in windows]
        self.front = front if front is not None else (self.scene[0].hwnd if self.scene else 0)
        return self

    def add(self, hwnd, title, proc, front=False):
        """Open a window on top (and in front, if asked)."""
        from nova.platform import Window
        self.scene.insert(0, Window(hwnd, title, proc))
        if front:
            self.front = hwnd
        return self

    # -- the Desktop interface
    def windows(self):
        return list(self.scene)

    def foreground(self):
        return self.front

    def focus(self, hwnd):
        self.log.append(("focus", hwnd))
        w = next((w for w in self.scene if w.hwnd == hwnd), None)
        if w is not None:
            self.scene.remove(w)
            self.scene.insert(0, w)
            self.front = hwnd

    # keys and typing: only recorded
    def press(self, keys):
        self.log.append(("press", keys))

    def write(self, text, delay=0):
        self.log.append(("write", text))

    # the clipboard
    def clipboard_text(self):
        return self.clipboard if isinstance(self.clipboard, str) else ""

    def set_clipboard_text(self, text):
        self.log.append(("copy", text))
        self.clipboard = text
        self.clip_seq += 1

    def clipboard_save(self):
        if isinstance(self.clipboard, list):
            return list(self.clipboard)
        return [(13, (self.clipboard + "\0").encode("utf-16-le"))] if self.clipboard else []

    def clipboard_set_text(self, text):
        self.log.append(("clip", text))
        self.clipboard = text
        self.clip_seq += 1
        return self.clip_seq

    def clipboard_restore(self, saved, our_seq):
        self.log.append(("restore", saved, our_seq))
        if saved is not None and our_seq == self.clip_seq:
            self.clipboard = list(saved) if saved else ""

    def focused_text(self):
        return self.text_in_focus

    # the screen: plain attributes a test can set
    monitor = (0, 0, 1920, 1080)
    work = (0, 0, 1920, 1040)
    taskbar = None              # (edge, rect) when the taskbar auto-hides
    dark = False                # None = can't be read
    idle = 0.0
    locked = False

    def window_rect(self, hwnd):
        return getattr(self, "rects", {}).get(hwnd, (100, 100, 900, 700))

    def monitor_rect(self, hwnd):
        return self.monitor

    def work_area(self):
        return self.work

    def autohide_taskbar(self):
        return self.taskbar

    def dark_mode(self):
        return self.dark

    def idle_seconds(self):
        return self.idle

    def screen_locked(self):
        return self.locked

    def dpi_aware(self):
        return True

    # one window's state and actions
    def is_window(self, hwnd):
        return bool(hwnd) and (hwnd in self.alive or any(w.hwnd == hwnd for w in self.scene))

    def is_visible(self, hwnd):
        return any(w.hwnd == hwnd for w in self.scene)

    def is_minimized(self, hwnd):
        return hwnd in self.minimized

    def is_maximized(self, hwnd):
        return hwnd in self.maximized

    def is_hung(self, hwnd):
        return hwnd in self.hung

    def show(self, hwnd, cmd):
        self.log.append(("show", hwnd, cmd))
        if cmd in (2, 6, 7):                    # minimise
            self.minimized.add(hwnd)
            self.maximized.discard(hwnd)
        elif cmd == 9:                          # restore
            self.minimized.discard(hwnd)
            self.maximized.discard(hwnd)
        elif cmd == 3:                          # maximise
            self.minimized.discard(hwnd)
            self.maximized.add(hwnd)

    def close(self, hwnd):
        """WM_CLOSE: here the window simply goes (a test that wants "Save changes?" adds that window itself)."""
        self.log.append(("close", hwnd))
        self.alive.discard(hwnd)
        self.scene = [w for w in self.scene if w.hwnd != hwnd]
        if self.front == hwnd:
            self.front = self.scene[0].hwnd if self.scene else 0

    def window_pid(self, hwnd):
        return self.pids.get(hwnd, 1000 + int(hwnd or 0))

    def class_name(self, hwnd):
        return self.classes.get(hwnd, "")

    def title(self, hwnd):
        return next((w.title for w in self.scene if w.hwnd == hwnd), "")

    def dpi(self, hwnd):
        return 96

    def frame_rect(self, hwnd):
        return self.window_rect(hwnd)

    def move(self, hwnd, x, y, w, h, flags=0):
        self.log.append(("move", hwnd, (x, y, x + w, y + h)))
        self.rects = {**getattr(self, "rects", {}), hwnd: (x, y, x + w, y + h)}

    def set_placement(self, hwnd, rect, show_cmd):
        self.log.append(("placement", hwnd, tuple(rect), show_cmd))

    def virtual_screen(self):
        return self.virtual

    def top_level(self):
        return [(w.hwnd, self.class_name(w.hwnd), w.title, self.window_pid(w.hwnd), w.proc) for w in self.scene]

    def children(self, hwnd):
        return list(self.kids.get(hwnd, []))

    # the mouse: the pointer moves silently; buttons and the wheel are logged
    cursor = (0, 0)

    def cursor_pos(self):
        return self.cursor

    def set_cursor(self, x, y):
        self.cursor = (int(x), int(y))

    def mouse(self, flags, data=0):
        self.log.append(("mouse", flags, data, self.cursor))

    # the system: a reversible stand-in for DPAPI, and settings that are only recorded
    display_name = "Test User"
    memory = (4 * 2**30, 16 * 2**30)        # (used, limit) bytes

    def protect(self, data):
        return b"FAKE-DPAPI:" + bytes(data)[::-1]

    def unprotect(self, data):
        if not bytes(data).startswith(b"FAKE-DPAPI:"):
            raise OSError("Windows couldn't encrypt/decrypt the saved key.")
        return bytes(data)[len(b"FAKE-DPAPI:"):][::-1]

    def user_display_name(self):
        return self.display_name

    def memory_commit(self):
        return self.memory

    def set_wallpaper(self, path):
        self.log.append(("wallpaper", str(path)))
        return True

    def set_dark_mode(self, on):
        self.log.append(("dark_mode", bool(on)))
        self.dark = bool(on)
