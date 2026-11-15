"""Nova desktop app: native window + tray icon + Ctrl+Alt+N hotkey.

Run:  pythonw desktop.py            (the launcher in the project folder; or double-click "Start Nova.bat")
      pythonw desktop.py --hidden   (start in the tray, used for auto-start)
"""
import ctypes
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import keyboard
import pystray
import webview
from PIL import Image, ImageDraw
from werkzeug.serving import make_server

from nova.core import app as server
from nova.shared import ROOT, work_area

PORT = 5055
URL = f"http://127.0.0.1:{PORT}"
HOTKEY = "ctrl+alt+n"


def already_running():
    """If Nova is open already, ask it to show its window and exit."""
    try:
        urllib.request.urlopen(urllib.request.Request(URL + "/api/show_window", method="POST"), timeout=2)
        return True
    except Exception:
        return False


def icon_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(124, 92, 255))
    d.ellipse((18, 22, 26, 32), fill="white")
    d.ellipse((38, 22, 46, 32), fill="white")
    d.arc((20, 30, 44, 48), 20, 160, fill="white", width=4)
    return img


# The hidden live-3D renderer must keep drawing while its window is hidden: without these, WebView2 slows a hidden
# page's timers to one a second. (Measured: ~23 fps hidden with them, ~1 fps without; it renders on the Intel GPU.)
# The variable replaces pywebview's own browser arguments, so its ElasticOverscroll one is kept here.
WEBVIEW2_ARGS = ("--disable-features=ElasticOverscroll --disable-background-timer-throttling "
                 "--disable-renderer-backgrounding --disable-backgrounding-occluded-windows")


def main():
    if already_running():
        return
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", WEBVIEW2_ARGS)
    srv = make_server("127.0.0.1", PORT, server.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # Only 3D Nova shows on screen; the chat page waits in the tray (Ctrl+Alt+N / tray icon). --show opens it.
    hidden = "--show" not in sys.argv
    window = webview.create_window("Nova", URL, width=1250, height=820, min_size=(820, 600),
                                   hidden=hidden, background_color="#0d0f1a")
    # Nova's "ear": a small bubble next to 3D Nova that holds Windows voice typing while she listens.
    ear = webview.create_window("Nova ear", URL + "/ear", width=430, height=104, frameless=True, on_top=True,
                                hidden=True, resizable=False, background_color="#151829")
    # 3D Nova's live renderer: draws her model every frame and hands the pixels to avatar.py (see body.py).
    # Never shown; off in the tray menu ("Live 3D Nova") falls back to the saved sprite frames.
    webview.create_window("Nova body", URL + "/static/nova3d.html?live=1", width=300, height=380, hidden=True)
    quitting = {"v": False}
    ducked = {"v": False}
    from nova.actions import tools
    u32 = ctypes.windll.user32

    def chat_hwnd():
        return next((h for h, title, _ in tools._windows() if title == "Nova"), None)

    def chat_on_screen():
        """Ask Windows directly (no pywebview event callbacks: those deadlocked when other threads moved the window)."""
        h = chat_hwnd()
        return bool(h and u32.IsWindowVisible(h) and not u32.IsIconic(h))

    def show(*_):
        window.show()
        window.restore()
        h = chat_hwnd()
        if h:
            tools._focus(h)

    def on_closing():
        if quitting["v"]:
            return True
        window.hide()  # closing the window keeps Nova alive in the tray
        return False

    # When Nova looks at the screen, her chat window steps aside so she sees your apps, then comes back.
    def duck():
        ducked["v"] = chat_on_screen()
        if ducked["v"]:
            window.hide()

    def unduck():
        if ducked["v"]:
            window.show()
        ducked["v"] = False

    tools.SCREEN_HOOKS.update(hide=duck, show=unduck)

    # 3D Nova walking around the desktop runs as her own process (avatar.py).
    from nova.avatar import avatar_client

    def show_ear():
        """Put the listening bubble beside 3D Nova and give it the keyboard focus (voice typing types there)."""
        x, y = 1200, 900
        pos = avatar_client.position()
        if pos:
            fx, fy = pos
            left, top, right, bottom = work_area()
            x = int(fx - 120 - 430) if fx - 120 - 430 > left else int(fx + 120)
            y = int(min(max(fy - 190, top), bottom - 110))
        ear.move(x, y)
        ear.show()
        h = next((h for h, title, _ in tools._windows() if title == "Nova ear"), None)
        if h:
            tools._focus(h)

    server.ear_hooks.update(show=show_ear, hide=ear.hide)
    body = {"proc": None, "shown": True}
    if not avatar_client.running():
        pyw = Path(sys.executable).with_name("pythonw.exe")
        body["proc"] = subprocess.Popen([str(pyw if pyw.exists() else sys.executable), "-m", "nova.avatar.avatar"], cwd=str(ROOT))
    server.avatar_reload_hook = lambda: avatar_client.send("reload", timeout=5)

    def toggle_body(icon, _):
        body["shown"] = not body["shown"]
        avatar_client.send("show" if body["shown"] else "hide", timeout=3)

    def quit_app(icon, _=None):
        quitting["v"] = True
        icon.stop()
        keyboard.unhook_all()
        if body["proc"]:
            body["proc"].terminate()
        window.destroy()
        srv.shutdown()

    def toggle_live(icon, _):
        s = server.settings()
        s["live_3d"] = not s.get("live_3d", True)
        server.save_settings(s)
        avatar_client.send("live", on=s["live_3d"], timeout=3)

    def toggle_hearing(icon, _):
        s = server.settings()
        s["react_to_sound"] = not s.get("react_to_sound", True)
        server.save_settings(s)          # avatar.py's ears read it within a few seconds

    def toggle_autostart(icon, _):
        server.set_autostart(not server.STARTUP_LNK.exists())

    window.events.closing += on_closing
    server.show_window_hook = show
    server.ears.set_mode("typing")   # after the wake word, the app listens (silent recognizer, or the bubble)
    threading.Thread(target=server.warm_brain, daemon=True).start()   # so the first answer isn't the slow one
    if server.settings()["wake"]:
        server.ears.start()
    server.start_background()       # nap clock, suggestions about the screen, notifications
    keyboard.add_hotkey(HOTKEY, show)

    tray = pystray.Icon("Nova", icon_image(), "Nova - Ctrl+Alt+N", menu=pystray.Menu(
        pystray.MenuItem("Open Nova", show, default=True),
        pystray.MenuItem("Stop what Nova is doing", lambda icon, _: server.stop_now()),
        pystray.MenuItem("Show 3D Nova on desktop", toggle_body, checked=lambda _: body["shown"]),
        pystray.MenuItem("Live 3D Nova (new moves and looks)", toggle_live,
                         checked=lambda _: server.settings().get("live_3d", True)),
        pystray.MenuItem("Dance to music playing on the laptop", toggle_hearing,
                         checked=lambda _: server.settings().get("react_to_sound", True)),
        pystray.MenuItem("Start with Windows", toggle_autostart, checked=lambda _: server.STARTUP_LNK.exists()),
        pystray.MenuItem("Quit Nova", quit_app),
    ))
    tray.run_detached()
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()
