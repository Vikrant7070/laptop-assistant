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

    def close(self, hwnd):
        self.scene = [w for w in self.scene if w.hwnd != hwnd]
        if self.front == hwnd:
            self.front = self.scene[0].hwnd if self.scene else 0

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
