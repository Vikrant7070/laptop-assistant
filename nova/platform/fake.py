"""A pretend desktop for tests: windows in z-order, a front window, and a log of what Nova did to them."""


class FakeDesktop:
    """FakeDesktop([(hwnd, title, proc), ...], front=hwnd). The list is topmost first; front defaults to the first.

    `log` records actions as tuples, e.g. ("focus", 2), so a test can check what Nova did without patching her
    private helpers.
    """

    def __init__(self, windows=(), front=None):
        self.scene = []
        self.log = []
        self.set_windows(windows, front)

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
