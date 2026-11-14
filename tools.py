"""Laptop-control tools for the assistant (Windows).

Each tool is a plain function returning a string. TOOLS holds the JSON schemas
sent to Claude; RISKY names the tools that need the user's click-to-approve
before they run.
"""
import datetime as dt
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import psutil

from shared import DATA, REACTIONS, ps_escape, work_area

try:  # real pixel coordinates, so clicks land where UI Automation says
    import ctypes as _c
    _c.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

HOME = Path.home()
DATA.mkdir(parents=True, exist_ok=True)
NOTES_FILE = DATA / "notes.json"
REMINDERS_FILE = DATA / "reminders.json"
MEMORY_FILE = DATA / "memory.json"


def _load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


_files_lock = threading.Lock()   # notes / reminders are read-modify-written from several threads


def _save(path, obj):
    """Write JSON atomically (a reader never sees a half-written file)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _ps(cmd, timeout=30):
    """Run a PowerShell command and return its output."""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out = (r.stdout or "") + (("\nERR: " + r.stderr) if r.stderr.strip() else "")
    return out.strip()[:6000] or "(done, no output)"


def _fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _resolve(path):
    p = Path(os.path.expandvars(os.path.expanduser(path or "~")))
    shortcuts = {"desktop": HOME / "OneDrive" / "Desktop", "downloads": HOME / "Downloads",
                 "documents": HOME / "Documents", "pictures": HOME / "Pictures",
                 "music": HOME / "Music", "videos": HOME / "Videos", "home": HOME}
    key = str(path).strip().lower()
    if key in shortcuts:
        p = shortcuts[key]
        if key == "desktop" and not p.exists():
            p = HOME / "Desktop"
    return p


# ---------------------------------------------------------------- system info

def stats():
    """Small dict for the live dashboard."""
    bat = psutil.sensors_battery()
    disk = psutil.disk_usage(str(HOME.anchor))
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "ram": psutil.virtual_memory().percent,
        "disk": disk.percent,
        "battery": round(bat.percent) if bat else None,
        "plugged": bat.power_plugged if bat else None,
        "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1),
    }


def get_system_status():
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(HOME.anchor))
    bat = psutil.sensors_battery()
    lines = [
        f"Computer: {platform.node()} ({platform.system()} {platform.release()})",
        f"CPU: {psutil.cpu_percent(interval=0.5)}% used, {psutil.cpu_count()} logical cores",
        f"RAM: {vm.percent}% used ({_fmt_bytes(vm.used)} of {_fmt_bytes(vm.total)})",
        f"Disk {HOME.anchor}: {disk.percent}% used, {_fmt_bytes(disk.free)} free of {_fmt_bytes(disk.total)}",
        f"Uptime: {round((time.time() - psutil.boot_time()) / 3600, 1)} hours",
    ]
    if bat:
        mins = bat.secsleft // 60 if bat.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) else None
        lines.append(f"Battery: {round(bat.percent)}%, {'charging' if bat.power_plugged else 'on battery'}"
                     + (f", about {mins} min left" if mins and not bat.power_plugged else ""))
    return "\n".join(lines)


def list_top_processes(sort_by="memory", limit=10):
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
        try:
            procs.append(p.info)
        except psutil.Error:
            pass
    if sort_by == "cpu":
        for p in procs:
            try:
                p["cpu_percent"] = psutil.Process(p["pid"]).cpu_percent(interval=0.05)
            except psutil.Error:
                p["cpu_percent"] = 0
        procs.sort(key=lambda p: p["cpu_percent"] or 0, reverse=True)
    else:
        procs.sort(key=lambda p: p["memory_info"].rss if p["memory_info"] else 0, reverse=True)
    rows = [f"{p['name']} (pid {p['pid']}): {_fmt_bytes(p['memory_info'].rss) if p['memory_info'] else '?'} RAM, {p['cpu_percent'] or 0}% CPU"
            for p in procs[: int(limit)]]
    return "\n".join(rows)


def _nova_pids():
    """This process, its parent and its children (3D body, listeners) - never closed by kill_process."""
    pids = {os.getpid()}
    try:
        me = psutil.Process()
        pids |= {c.pid for c in me.children(recursive=True)}
        if me.parent():
            pids.add(me.parent().pid)
    except psutil.Error:
        pass
    return pids


def kill_process(name_or_pid):
    killed, mine = [], _nova_pids()
    target = str(name_or_pid).lower().removesuffix(".exe")
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if p.info["pid"] in mine:
                continue
            if str(p.info["pid"]) == target or (p.info["name"] or "").lower().removesuffix(".exe") == target:
                p.terminate()
                killed.append(f"{p.info['name']} ({p.info['pid']})")
        except psutil.Error as e:
            killed.append(f"could not close {p.info['name']}: {e}")
    return ("Closed: " + ", ".join(killed)) if killed else f"No running process matched '{name_or_pid}'."


def network_info():
    ip = "unknown"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        online = "online"
    except OSError:
        online = "offline"
    wifi = _ps("netsh wlan show interfaces | Select-String -Pattern ' SSID|Signal|State' | ForEach-Object { $_.ToString().Trim() }")
    return f"Internet: {online}\nLocal IP: {ip}\n{wifi}"


def get_datetime():
    return dt.datetime.now().strftime("%A, %d %B %Y, %I:%M %p")


# ---------------------------------------------------------------- apps & web

APPS = {
    "notepad": "notepad", "calculator": "calc", "calc": "calc", "paint": "mspaint",
    "explorer": "explorer", "file explorer": "explorer", "files": "explorer",
    "settings": "ms-settings:", "task manager": "taskmgr", "cmd": "cmd", "terminal": "wt",
    "powershell": "powershell", "control panel": "control", "camera": "microsoft.windows.camera:",
    "chrome": "chrome", "edge": "msedge", "firefox": "firefox", "word": "winword",
    "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook", "vscode": "code",
    "vs code": "code", "spotify": "spotify:", "store": "ms-windows-store:", "snipping tool": "snippingtool",
    "clock": "ms-clock:", "photos": "ms-photos:", "mail": "outlookmail:", "whatsapp": "whatsapp:",
}


def open_app(name):
    key = str(name).strip().lower()
    target = APPS.get(key, str(name).strip())
    if not target or re.search(r'[&|<>^"%\r\n]', target):
        return f"Couldn't open {name}: that isn't an app name."
    try:
        exe = shutil.which(target)
        if exe and exe.lower().endswith(".exe"):
            subprocess.Popen([exe])     # an argument list: no shell, nothing to inject into
        else:
            # Like the Run box: App Paths (chrome, winword), URL protocols (ms-settings:), .cmd launchers, files.
            os.startfile(exe or target)
        return f"Opened {name}."
    except Exception as e:
        return f"Couldn't open {name}: {e}"


BROWSERS = {"chrome.exe": "chrome", "msedge.exe": "msedge", "firefox.exe": "firefox", "brave.exe": "brave"}


def _exe_path(proc_name):
    """Full path of a running program, e.g. chrome.exe -> C:\\...\\chrome.exe (None if it isn't running)."""
    for p in psutil.process_iter(["name", "exe"]):
        try:
            if (p.info["name"] or "").lower() == proc_name and p.info["exe"]:
                return p.info["exe"]
        except psutil.Error:
            pass
    return None


def _browser_in_use():
    """The browser whose window is closest to the front - so Nova keeps using the one the user is in."""
    for _, _, proc in _windows():
        if proc.lower() in BROWSERS:
            return proc.lower()
    return None


def open_website(url_or_search):
    q = str(url_or_search).strip()
    if " " in q or "." not in q or q.lower().startswith(("file:", "javascript:")):
        url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)
    else:
        url = q if q.lower().startswith(("http://", "https://")) else "https://" + q
        url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%~")   # stray spaces / quotes can't break out
    browser = _browser_in_use()
    exe = _exe_path(browser) if browser else None
    if exe:   # keep using the browser the user is in (an argument list: no shell, nothing to inject into)
        subprocess.Popen([exe, url])
    else:
        webbrowser.open(url)
    return f"Opened {url}" + (f" in {BROWSERS[browser]}" if exe else "")


def open_windows_summary(limit=6):
    """A one-line view of what's open, newest/front first - given to Nova with each request."""
    names = [t[:70] for _, t, p in _windows()
             if t not in ("Nova", "Nova 3D", "Nova ear", "tk", "Program Manager") and p.lower() != "textinputhost.exe"]
    return "; ".join(f"'{n}'" for n in names[:limit])


def open_path(path):
    p = _resolve(path)
    if not p.exists():
        return f"{p} does not exist."
    os.startfile(str(p))
    return f"Opened {p}"


# ---------------------------------------------------------------- files

def list_folder(path="downloads"):
    p = _resolve(path)
    if not p.is_dir():
        return f"{p} is not a folder."
    entries = []
    for i in p.iterdir():
        try:
            st = i.stat()
            entries.append((st.st_mtime, i, st.st_size, i.is_dir()))
        except OSError:
            entries.append((0, i, 0, False))     # a locked or broken entry is still listed
    entries.sort(key=lambda e: e[0], reverse=True)
    rows = [f"{'[DIR] ' if d else ''}{i.name}{'' if d else ' - ' + _fmt_bytes(size)}" for _, i, size, d in entries[:60]]
    return f"{p} (newest first):\n" + "\n".join(rows)


def search_files(name_contains, folder="home", max_results=25):
    root = _resolve(folder)
    needle = name_contains.lower()
    hits, start = [], time.time()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("AppData", "node_modules", "$Recycle.Bin")]
        for f in filenames + dirnames:
            if needle in f.lower():
                hits.append(os.path.join(dirpath, f))
                if len(hits) >= int(max_results):
                    return "\n".join(hits)
        if time.time() - start > 20:
            hits.append("(stopped searching after 20 seconds)")
            break
    return "\n".join(hits) or f"Nothing named like '{name_contains}' in {root}."


def find_large_files(folder="home", min_mb=200):
    root = _resolve(folder)
    big, start = [], time.time()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "$Recycle.Bin"]
        for f in filenames:
            try:
                fp = os.path.join(dirpath, f)
                size = os.path.getsize(fp)
                if size >= float(min_mb) * 1024 * 1024:
                    big.append((size, fp))
            except OSError:
                pass
        if time.time() - start > 25:
            break
    big.sort(reverse=True)
    return "\n".join(f"{_fmt_bytes(s)}  {p}" for s, p in big[:30]) or f"No files over {min_mb} MB in {root}."


CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".svg"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".odt", ".md"},
    "Videos": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "Music": {".mp3", ".wav", ".flac", ".m4a", ".aac"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Installers": {".exe", ".msi"},
    "Code": {".py", ".js", ".html", ".css", ".json", ".ipynb", ".java", ".cpp", ".c"},
}


def _organize_plan(folder):
    p = _resolve(folder)
    plan = []
    for f in p.iterdir():
        if f.is_file():
            cat = next((c for c, exts in CATEGORIES.items() if f.suffix.lower() in exts), "Other")
            plan.append((f, p / cat / f.name))
    return p, plan


def preview_organize_folder(folder="downloads"):
    p, plan = _organize_plan(folder)
    if not plan:
        return f"No loose files in {p}."
    counts = {}
    for _, dest in plan:
        counts[dest.parent.name] = counts.get(dest.parent.name, 0) + 1
    return f"{len(plan)} files in {p} would be sorted into: " + ", ".join(f"{k} ({v})" for k, v in counts.items())


def organize_folder(folder="downloads"):
    p, plan = _organize_plan(folder)
    moved = 0
    for src, dest in plan:
        dest.parent.mkdir(exist_ok=True)
        final = dest
        n = 1
        while final.exists():
            final = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
            n += 1
        try:
            shutil.move(str(src), str(final))
            moved += 1
        except OSError:
            pass
    return f"Moved {moved} files in {p} into category folders."


def create_folder(path):
    p = _resolve(path)
    p.mkdir(parents=True, exist_ok=True)
    return f"Created {p}"


def delete_to_recycle_bin(path):
    p = _resolve(path)
    if not p.exists():
        return f"{p} does not exist."
    kind = "DeleteDirectory" if p.is_dir() else "DeleteFile"
    cmd = (f"Add-Type -AssemblyName Microsoft.VisualBasic; "
           f"[Microsoft.VisualBasic.FileIO.FileSystem]::{kind}('{ps_escape(p)}', 'OnlyErrorDialogs', 'SendToRecycleBin')")
    out = _ps(cmd)
    return f"Moved {p} to the Recycle Bin. {out if 'ERR' in out else ''}".strip()


def temp_files_size():
    total = 0
    for root in {os.environ.get("TEMP", ""), r"C:\Windows\Temp"}:
        for dirpath, _, files in os.walk(root):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
    return f"Temporary files take about {_fmt_bytes(total)}."


def clean_temp_files():
    root = os.environ.get("TEMP", "").strip()
    # Without a real TEMP folder, Path("") would be the current folder - never clean that (or a drive or home).
    if not root or not os.path.isabs(root) or not Path(root).is_dir() or Path(root) in (Path(Path(root).anchor), HOME):
        return "I couldn't find the temporary-files folder (TEMP), so I didn't delete anything."
    freed = 0
    for entry in Path(root).iterdir():
        try:
            size = entry.stat().st_size if entry.is_file() else sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
            if time.time() - entry.stat().st_mtime < 3600:
                continue  # skip files in use / very recent
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
            freed += size
        except Exception:
            pass
    return f"Cleaned temporary files and freed about {_fmt_bytes(freed)}. Files in use were skipped."


def empty_recycle_bin():
    return _ps("Clear-RecycleBin -Force -ErrorAction SilentlyContinue; 'Recycle Bin emptied.'")


# ---------------------------------------------------------------- controls

def set_volume(action, steps=5):
    keys = {"up": 175, "down": 174, "mute": 173, "unmute": 173}
    if action not in keys:
        return "action must be up, down, mute or unmute"
    n = 1 if action in ("mute", "unmute") else int(steps)
    _ps(f"$w = New-Object -ComObject WScript.Shell; 1..{n} | ForEach-Object {{ $w.SendKeys([char]{keys[action]}) }}")
    return f"Volume {action}" + (f" by {n} steps." if n > 1 else ".")


def media_control(action):
    keys = {"play_pause": 179, "next": 176, "previous": 177, "stop": 178}
    if action not in keys:
        return "action must be play_pause, next, previous or stop"
    _ps(f"(New-Object -ComObject WScript.Shell).SendKeys([char]{keys[action]})")
    return f"Media: {action.replace('_', '/')}."


def set_brightness(percent):
    pct = max(0, min(100, int(percent)))
    out = _ps(f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{pct})")
    return f"Brightness set to {pct}%." if "ERR" not in out else f"Couldn't change brightness here: {out}"


def lock_screen():
    subprocess.Popen("rundll32.exe user32.dll,LockWorkStation")
    return "Locked the screen."


def power_action(action):
    cmds = {"shutdown": "shutdown /s /t 30", "restart": "shutdown /r /t 30",
            "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0", "cancel": "shutdown /a"}
    if action not in cmds:
        return "action must be shutdown, restart, sleep or cancel"
    subprocess.Popen(cmds[action], shell=True)
    if action in ("shutdown", "restart"):
        return f"{action.title()} in 30 seconds. Say 'cancel shutdown' to stop it."
    return "Done." if action == "cancel" else "Going to sleep."


def run_powershell(command, as_admin=False):
    if not as_admin:
        return _ps(command, timeout=120)
    # Elevated: Windows shows its own UAC prompt; output comes back through a temp file.
    out = Path(os.environ.get("TEMP", ".")) / f"nova_admin_{int(time.time())}.txt"
    script = out.with_suffix(".ps1")
    script.write_text(f"& {{ {command} }} *> '{ps_escape(out)}'", encoding="utf-8")
    _ps(f"Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
        f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"{ps_escape(script)}\"'", timeout=300)
    try:
        raw = out.read_bytes() if out.exists() else b""
        result = raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8", errors="ignore")
    finally:
        script.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
    return (result.strip()[:6000] or "(ran as administrator, no output - or the UAC prompt was declined)")


# ---------------------------------------------------------------- windows, keyboard, screen (full access)

import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32


def _windows():
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if _user32.IsWindowVisible(hwnd):
            n = _user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                _user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wintypes.DWORD()
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    proc = psutil.Process(pid.value).name()
                except psutil.Error:
                    proc = "?"
                if buf.value not in ("Program Manager", "Settings") or proc != "explorer.exe":
                    found.append((hwnd, buf.value, proc))
        return True

    _user32.EnumWindows(cb, 0)
    return found


def list_windows():
    return "\n".join(f"{t}  [{p}]" for _, t, p in _windows()) or "No open windows."


def _find_window(title_or_app):
    q = title_or_app.lower().removesuffix(".exe")
    for w in _windows():
        if q in w[1].lower() or q == w[2].lower().removesuffix(".exe"):
            return w
    return None


def _focus(hwnd):
    """Bring a window to the front without pressing keys (borrow the foreground thread's input)."""
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, 9)
    fg = _user32.GetForegroundWindow()
    me = ctypes.windll.kernel32.GetCurrentThreadId()
    other = _user32.GetWindowThreadProcessId(fg, None)
    _user32.AttachThreadInput(me, other, True)
    _user32.BringWindowToTop(hwnd)
    _user32.SetForegroundWindow(hwnd)
    _user32.AttachThreadInput(me, other, False)


def window_action(title_or_app, action):
    w = _find_window(title_or_app)
    if not w:
        return f"No open window matches '{title_or_app}'."
    hwnd, title, _ = w
    if action == "focus":
        _focus(hwnd)
    elif action == "minimize":
        _user32.ShowWindow(hwnd, 6)
    elif action == "maximize":
        _user32.ShowWindow(hwnd, 3)
    elif action == "close":
        _user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE: app may ask to save
    else:
        return "action must be focus, minimize, maximize or close"
    return f"{action.title()}d '{title}'." if action != "focus" else f"Switched to '{title}'."


def minimize_all_windows():
    keyboard.send("windows+d")
    return "Showing the desktop."


import keyboard  # noqa: E402  (pure-Python global keyboard access)
import pyperclip  # noqa: E402


def type_text(text, into_window=None):
    if into_window:
        msg = window_action(into_window, "focus")
        if msg.startswith("No open"):
            return msg
        time.sleep(0.4)
    keyboard.write(text, delay=0.01)
    return f"Typed {len(text)} characters."


def press_keys(keys):
    keyboard.send(keys)
    return f"Pressed {keys}."


def get_clipboard():
    return pyperclip.paste()[:4000] or "(clipboard is empty)"


def set_clipboard(text):
    pyperclip.copy(text)
    return "Copied to the clipboard."


def take_screenshot():
    from PIL import ImageGrab
    folder = HOME / "Pictures" / "Nova Screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screenshot_{dt.datetime.now():%Y-%m-%d_%H-%M-%S}.png"
    ImageGrab.grab(all_screens=True).save(path)
    return f"Saved screenshot to {path}"


# ---------------------------------------------------------------- files (read / write / move)

def read_text_file(path, max_chars=6000):
    p = _resolve(path)
    if not p.is_file():
        return f"{p} is not a file."
    return p.read_text(encoding="utf-8", errors="replace")[: int(max_chars)]


def write_text_file(path, content, append=False):
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    return f"{'Appended to' if append else 'Wrote'} {p}"


def move_or_rename(source, destination):
    src, dst = _resolve(source), _resolve(destination)
    if not src.exists():
        return f"{src} does not exist."
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        return f"{dst} already exists - pick another name."
    shutil.move(str(src), str(dst))
    return f"Moved {src} -> {dst}"


def copy_path(source, destination):
    src, dst = _resolve(source), _resolve(destination)
    if not src.exists():
        return f"{src} does not exist."
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        return f"{dst} already exists - pick another name."
    (shutil.copytree if src.is_dir() else shutil.copy2)(str(src), str(dst))
    return f"Copied to {dst}"


# ---------------------------------------------------------------- apps & Windows settings

def search_installable_apps(query):
    return _ps(f"winget search --accept-source-agreements -- '{ps_escape(query)}' | Select-Object -First 15", timeout=90)


def install_app(winget_id):
    return _ps(f"winget install --id '{ps_escape(winget_id)}' -e --silent --accept-package-agreements "
               "--accept-source-agreements", timeout=900)


def uninstall_app(name_or_id):
    return _ps(f"winget uninstall --silent --accept-source-agreements -- '{ps_escape(name_or_id)}'", timeout=600)


def _like_literal(text):
    """Wildcard characters in the user's text are matched literally by -like."""
    return re.sub(r"([*?\[\]`])", r"`\1", str(text or ""))


def list_installed_apps(name_contains=""):
    cmd = ("Get-ItemProperty HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*,"
           "HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*,"
           "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\* -ErrorAction SilentlyContinue | "
           f"Where-Object {{ $_.DisplayName -like '*{ps_escape(_like_literal(name_contains))}*' }} | Sort-Object DisplayName -Unique | "
           "Select-Object -First 60 -ExpandProperty DisplayName")
    return _ps(cmd)


def list_startup_apps():
    return _ps("Get-CimInstance Win32_StartupCommand | Select-Object Name, Command | Format-Table -AutoSize | Out-String -Width 200")


SETTINGS_PAGES = {
    "wifi": "network-wifi", "bluetooth": "bluetooth", "display": "display", "sound": "sound",
    "battery": "batterysaver", "power": "powersleep", "updates": "windowsupdate", "apps": "appsfeatures",
    "startup": "startupapps", "storage": "storagesense", "privacy": "privacy", "personalization": "personalization",
    "background": "personalization-background", "notifications": "notifications", "mouse": "mousetouchpad",
    "keyboard": "typing", "time": "dateandtime", "accounts": "yourinfo", "default apps": "defaultapps",
    "night light": "nightlight", "vpn": "network-vpn", "hotspot": "network-mobilehotspot",
}


def open_settings_page(page):
    key = SETTINGS_PAGES.get(page.lower().strip(), page.lower().strip())
    os.startfile(f"ms-settings:{key}")
    return f"Opened Settings > {page}."


def set_dark_mode(enabled):
    v = 0 if enabled in (True, "true", "on", 1) else 1
    for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
        _ps(f"Set-ItemProperty HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize -Name {name} -Value {v}")
    return f"Dark mode {'on' if v == 0 else 'off'}."


def set_wallpaper(image_path):
    p = _resolve(image_path)
    if not p.is_file():
        return f"{p} is not an image file."
    ctypes.windll.user32.SystemParametersInfoW(20, 0, str(p), 3)
    return f"Wallpaper set to {p.name}."


# ---------------------------------------------------------------- clicking like a human (UI Automation + Nova walks there)

CLICKABLE = {"ButtonControl", "MenuItemControl", "HyperlinkControl", "TabItemControl", "ListItemControl",
             "CheckBoxControl", "RadioButtonControl", "EditControl", "ComboBoxControl", "TreeItemControl",
             "SplitButtonControl", "MenuBarControl", "ImageControl"}


def _target_window(window):
    if window:
        return _find_window(window)
    for w in _windows():  # z-order: topmost first; skip Nova herself
        if w[1] not in ("Nova", "Nova 3D", "Nova ear", "tk") and w[2].lower() not in ("pythonw.exe", "textinputhost.exe"):
            return w
    return None


def _scan(window, limit=200, offscreen=False, contains=""):
    """Clickable things in a window, in page order. offscreen=True also returns items scrolled out of view."""
    import uiautomation as auto
    w = _target_window(window)
    if not w:
        return None, []
    root = auto.ControlFromHandle(w[0])
    items, q = [], contains.lower().strip()
    # "video" = video links: on YouTube (and most video sites) their names end with the length, e.g. "… 4 minutes, 22 seconds"
    video = q in ("video", "videos", "views", "song", "songs")
    duration = re.compile(r"\b\d+ (second|minute|hour)s?\b", re.I)
    for ctrl, _depth in auto.WalkControl(root, maxDepth=40):
        if len(items) >= limit:
            break
        try:
            if ctrl.ControlTypeName not in CLICKABLE:
                continue
            hidden = ctrl.IsOffscreen
            if hidden and not offscreen:
                continue
            name = (ctrl.Name or "").strip()
            if not name and ctrl.ControlTypeName != "EditControl":
                continue
            if video:
                if ctrl.ControlTypeName != "HyperlinkControl" or not duration.search(name):
                    continue
            elif q and q not in name.lower():
                continue
            r = ctrl.BoundingRectangle
            if (r.width() <= 0 or r.height() <= 0) and not hidden:
                continue
            items.append({"name": name or "(text box)", "type": ctrl.ControlTypeName.replace("Control", ""),
                          "x": (r.left + r.right) // 2, "y": (r.top + r.bottom) // 2, "offscreen": hidden, "ctrl": ctrl})
        except Exception:
            continue
    return w, items


def list_clickable(window="", contains=""):
    w, items = _scan(window, limit=300, contains=contains)
    if not w:
        return f"No open window matches '{window}'."
    if not items:
        return f"I couldn't find clickable items{' containing ' + repr(contains) if contains else ''} in '{w[1]}'."
    lines = [f"{n}. {i['name'][:70]} [{i['type']}]" for n, i in enumerate(items[:60], 1)]
    return f"Clickable items in '{w[1]}' (top of page first):\n" + "\n".join(lines)


def _bring_into_view(item, w):
    """Scroll an off-screen item into view and return its fresh centre point."""
    ctrl = item["ctrl"]
    try:
        ctrl.GetScrollItemPattern().ScrollIntoView()
    except Exception:
        for _ in range(15):  # no scroll pattern: wheel down until it shows up
            if not ctrl.IsOffscreen:
                break
            scroll("down", 5, w[1])
            time.sleep(0.25)
    time.sleep(0.6)
    r = ctrl.BoundingRectangle
    return (r.left + r.right) // 2, (r.top + r.bottom) // 2


def scroll(direction="down", amount=5, window=""):
    """Turn the mouse wheel over a window (amount = wheel notches; ~5 is about half a page)."""
    w = _target_window(window)
    if w:
        r = wintypes.RECT()
        _user32.GetWindowRect(w[0], ctypes.byref(r))
        _focus(w[0])
        _move_mouse_smoothly((r.left + r.right) // 2, (r.top + r.bottom) // 2 + 60, 0.2)
    sign = -1 if direction == "down" else 1
    for _ in range(max(1, min(int(amount), 40))):
        _user32.mouse_event(0x0800, 0, 0, sign * 120, 0)   # MOUSEEVENTF_WHEEL
        time.sleep(0.04)
    return f"Scrolled {direction} {amount} notches" + (f" in '{w[1]}'." if w else ".")


def fill_in(text, box="", window="", press_enter=True):
    """Click a text box (e.g. a website's Search box), replace its text, and press Enter - one reliable step."""
    w, items = _scan(window, limit=500)
    if not w:
        return f"No open window matches '{window}'."
    boxes = [i for i in items if i["type"] in ("Edit", "ComboBox")]
    q = box.lower().strip()
    if q:
        match = (next((i for i in boxes if i["name"].lower() == q), None)
                 or next((i for i in boxes if q in i["name"].lower()), None))
    else:  # no name given: the page's own search/text box, not the browser's address bar
        page = [i for i in boxes if "address" not in i["name"].lower()]
        match = next((i for i in page if "search" in i["name"].lower()), None) or (page[0] if page else None)
    if not match:
        return f"No text box{' named ' + repr(box) if box else ''} in '{w[1]}'. Boxes I see: " + \
            ", ".join(i["name"] for i in boxes)[:500]
    _focus(w[0])
    _nova_click(match["x"], match["y"], label=match["name"])
    time.sleep(0.3)
    keyboard.send("ctrl+a")
    time.sleep(0.1)
    keyboard.write(text, delay=0.02)
    if press_enter:
        time.sleep(0.2)
        keyboard.send("enter")
    return f"Typed '{text}' into '{match['name']}'" + (" and pressed Enter." if press_enter else ".")


# Video player buttons (YouTube names them like "Full screen (f)", "Pause keyboard shortcut k", "Mute (m)").
VIDEO_BUTTONS = {
    "fullscreen": ["full screen", "fullscreen"],
    "exit_fullscreen": ["exit full screen", "exit fullscreen"],
    "pause": ["pause"],
    "play": ["play"],
    "next": ["next"],
    "mute": ["mute"],
    "unmute": ["unmute"],
    "captions": ["subtitles", "captions"],
    "theater": ["theater mode", "default view"],
}
VIDEO_SITES = ("youtube", "netflix", "prime video", "hotstar", "jiocinema", "vimeo", "twitch")


def _video_window():
    for w in _windows():
        if any(s in w[1].lower() for s in VIDEO_SITES):
            return w
    return None


def video_control(action):
    """Press a video player's own button: fullscreen, exit_fullscreen, play, pause, next, mute, unmute, captions, theater."""
    if action not in VIDEO_BUTTONS:
        return f"action must be one of: {', '.join(VIDEO_BUTTONS)}"
    w = _video_window()
    if not w:
        return "I don't see a video open (YouTube, Netflix, ...)."
    _, items = _scan(w[1], limit=2000)
    buttons = [i for i in items if i["type"] == "Button"]
    wanted = VIDEO_BUTTONS[action]

    def find(words, avoid=()):
        for i in buttons:
            n = i["name"].lower()
            if any(n.startswith(k) for k in words) and not any(n.startswith(a) for a in avoid):
                return i
        return None

    # The same button changes name with its state (Pause <-> Play, Full screen <-> Exit full screen, Mute <-> Unmute).
    if action == "fullscreen" and find(VIDEO_BUTTONS["exit_fullscreen"]):
        return "The video is already full screen."
    if action == "exit_fullscreen" and not find(VIDEO_BUTTONS["exit_fullscreen"]):
        return "The video isn't full screen."
    if action == "pause" and not find(["pause"]) and find(["play"]):
        return "The video is already paused."
    if action == "play" and find(["pause"]):
        return "The video is already playing."
    if action == "mute" and find(["unmute"]):
        return "The video is already muted."
    match = find(wanted, avoid=("exit",) if action == "fullscreen" else ("unmute",) if action == "mute" else ())
    if not match:
        return f"I couldn't find the {action.replace('_', ' ')} button on '{w[1][:60]}'."
    _focus(w[0])
    time.sleep(0.2)
    _move_mouse_smoothly(match["x"], match["y"] - 120, 0.2)  # wake the player so its buttons show
    time.sleep(0.3)
    _nova_click(match["x"], match["y"], label=match["name"])
    return f"Pressed '{match['name']}' on '{w[1][:60]}'."


def click_nth(contains, number, window=""):
    """Click the Nth clickable item (in page order) whose name contains some text - e.g. the 7th YouTube video."""
    w, items = _scan(window, limit=500, offscreen=True, contains=contains)
    if not w:
        return f"No open window matches '{window}'."
    n = int(number)
    if n < 1 or n > len(items):
        return f"There are only {len(items)} items containing '{contains}' in '{w[1]}'."
    item = items[n - 1]
    _focus(w[0])
    x, y = _bring_into_view(item, w) if item["offscreen"] else (item["x"], item["y"])
    time.sleep(0.2)
    _nova_click(x, y, label=item["name"])
    return f"Clicked #{n} containing '{contains}': '{item['name'][:80]}' in '{w[1]}'."


def _move_mouse_smoothly(x, y, secs=0.35):
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    sx, sy = pt.x, pt.y
    steps = 22
    bend = (x - sx) * 0.08
    for i in range(1, steps + 1):
        t = i / steps
        e = t * t * (3 - 2 * t)  # ease in-out, like a hand
        _user32.SetCursorPos(int(sx + (x - sx) * e), int(sy + (y - sy) * e - bend * (t - t * t) * 4))
        time.sleep(secs / steps)


def _mouse_click(x, y, button="left", double=False):
    down, up = (0x0008, 0x0010) if button == "right" else (0x0002, 0x0004)
    _move_mouse_smoothly(x, y)
    for _ in range(2 if double else 1):
        _user32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.05)
        _user32.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.08)


def _nova_click(x, y, button="left", double=False, label=""):
    """Nova walks over, points at the spot, and the real mouse clicks it."""
    import avatar_client
    if avatar_client.running():
        if label:
            avatar_client.send("say", text=f"Clicking “{label[:40]}”!", secs=3)
        avatar_client.send("go_point", x=x, y=y, timeout=15)
        avatar_client.send("clickthrough", on=True)
    _mouse_click(x, y, button, double)
    if avatar_client.running():
        avatar_client.send("clickthrough", on=False)
        avatar_client.send("finish_point")


def click_element(name, window="", button="left", double=False):
    w, items = _scan(window, limit=500, offscreen=True)
    if not w:
        return f"No open window matches '{window}'."
    q = name.lower().strip()
    visible_first = sorted(items, key=lambda i: i["offscreen"])
    match = (next((i for i in visible_first if i["name"].lower() == q), None)
             or next((i for i in visible_first if i["name"].lower().startswith(q)), None)
             or next((i for i in visible_first if q in i["name"].lower()), None))
    if not match:
        sample = ", ".join(i["name"][:40] for i in items if not i["offscreen"])[:900]
        return f"No '{name}' in '{w[1]}'. Some things I can click: {sample}"
    _focus(w[0])
    time.sleep(0.3)
    x, y = _bring_into_view(match, w) if match["offscreen"] else (match["x"], match["y"])
    _nova_click(x, y, button, double, match["name"])
    return f"Clicked '{match['name']}' ({match['type']}) in '{w[1]}'."


def click_at(x, y, button="left", double=False):
    _nova_click(int(x), int(y), button, double)
    return f"Clicked at ({int(x)}, {int(y)})."


# ---------------------------------------------------------------- camera (Nova's eyes)

LAST_IMAGE = {"jpg_b64": None}  # shown in the chat so the user always sees what Nova looked at


def _setting(name, default=None):
    return _load(DATA / "settings.json", {}).get(name, default)


def _camera_allowed():
    return bool(_setting("camera"))


def _camera_users():
    """Other apps holding the webcam right now (Windows records this in its privacy settings)."""
    import winreg
    users = []
    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"

    def scan(path):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    sub = winreg.EnumKey(k, i)
                    if sub == "NonPackaged":
                        scan(path + "\\" + sub)
                        continue
                    try:
                        with winreg.OpenKey(k, sub) as s:
                            if winreg.QueryValueEx(s, "LastUsedTimeStop")[0] == 0:
                                users.append(sub)
                    except OSError:
                        pass
        except OSError:
            pass
    scan(base)
    nice = []
    for u in users:
        name = u.replace("#", "\\").split("\\")[-1]
        if "python" in name.lower():
            continue  # that's Nova herself
        name = re.sub(r"_[a-z0-9]{13}$", "", name)          # store-app suffix
        name = name.split(".")[-1] if "." in name and not name.endswith(".exe") else name
        nice.append(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).removesuffix(".exe"))
    return nice


def _grab_camera_jpeg(max_width=768):
    import base64
    import cv2
    busy = _camera_users()
    if busy:
        raise RuntimeError(f"The camera is being used by another app ({', '.join(busy)}). "
                           "Windows lets only one app use it at a time - close that app and ask me again.")
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise RuntimeError("I can't open the camera - another app may be using it, or it's switched off.")
    ok, frame = False, None
    for _ in range(10):  # let the camera adjust its exposure for a moment
        ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise RuntimeError("The camera didn't give me a picture.")
    if frame.mean() < 8:
        raise RuntimeError("The camera picture is completely black - the lens may be covered, the privacy shutter "
                           "closed, or another app is holding the camera.")
    h, w = frame.shape[:2]
    if w > max_width:
        frame = cv2.resize(frame, (max_width, int(h * max_width / w)))
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return base64.b64encode(buf.tobytes()).decode()


def look_through_camera(question="What do you see?"):
    if not _camera_allowed():
        return "The camera is switched off. The user can allow it in Nova's settings (⚙ Model → Camera)."
    import avatar_client
    import llm
    avatar_client.send("say", text="Let me take a look 👀", secs=3, timeout=2)
    img = _grab_camera_jpeg()  # kept in memory only, never saved to disk
    LAST_IMAGE["jpg_b64"] = img
    model = _setting("model")
    return "Through the camera: " + llm.see(img, question, preferred=model)


# ---------------------------------------------------------------- screen (Nova reads what's on your screen)

SCREEN_HOOKS = {"hide": None, "show": None}  # desktop.py hides Nova's chat window while she looks


def _screen_allowed():
    return bool(_setting("screen"))


def _grab_screen_jpeg(what="screen", window="", max_width=1400):
    import base64
    import io
    from PIL import ImageGrab
    import avatar_client
    bbox = None
    if what == "window":
        w = _target_window(window)
        if not w:
            raise RuntimeError(f"No open window matches '{window}'.")
        r = wintypes.RECT()
        _user32.GetWindowRect(w[0], ctypes.byref(r))
        bbox = (max(r.left, 0), max(r.top, 0), r.right, r.bottom)
    # Step out of the way so the picture shows the user's stuff, not Nova.
    avatar_client.send("duck", timeout=2)
    if SCREEN_HOOKS["hide"]:
        SCREEN_HOOKS["hide"]()
    time.sleep(0.45)
    try:
        img = ImageGrab.grab(bbox=bbox, all_screens=bbox is None)
    finally:
        if SCREEN_HOOKS["show"]:
            SCREEN_HOOKS["show"]()
        avatar_client.send("unduck", timeout=2)
    if img.width > max_width:
        img = img.resize((max_width, int(img.height * max_width / img.width)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def look_at_screen(question="What's on my screen?", what="screen", window=""):
    if not _screen_allowed():
        return "Screen viewing is switched off. The user can allow it in Nova's settings (⚙ Model → Screen)."
    import llm
    img = _grab_screen_jpeg(what, window)  # kept in memory only, never saved to disk
    LAST_IMAGE["jpg_b64"] = img
    model = _setting("model")
    return "On the screen: " + llm.see(img, question, preferred=model, kind="screen")


def toggle_wifi(on):
    state = "enabled" if on in (True, "true", "on", 1) else "disabled"
    return run_powershell(f'netsh interface set interface name="Wi-Fi" admin={state}', as_admin=True)


# ---------------------------------------------------------------- notes, reminders, memory

def add_note(text):
    with _files_lock:
        notes = _load(NOTES_FILE, [])
        notes.append({"text": text, "at": get_datetime()})
        _save(NOTES_FILE, notes)
    return f"Saved note #{len(notes)}."


def list_notes():
    notes = _load(NOTES_FILE, [])
    return "\n".join(f"{i+1}. {n['text']} ({n['at']})" for i, n in enumerate(notes)) or "No notes yet."


def set_reminder(text, minutes_from_now):
    due = time.time() + float(minutes_from_now) * 60
    with _files_lock:
        rems = _load(REMINDERS_FILE, [])
        rems.append({"text": text, "due": due, "done": False})
        _save(REMINDERS_FILE, rems)
    return f"I'll remind you at {dt.datetime.fromtimestamp(due).strftime('%I:%M %p')}: {text}"


def due_reminders():
    with _files_lock:
        rems = _load(REMINDERS_FILE, [])
        now, due = time.time(), []
        for r in rems:
            if not r.get("done") and r.get("due", now + 1) <= now:
                r["done"] = True
                due.append(r.get("text", ""))
        if due:
            _save(REMINDERS_FILE, rems[-200:])    # finished reminders don't pile up forever
    return due


def remember_fact(fact):
    import knowledge
    return knowledge.update_profile("facts", fact)


def forget_fact(fact_contains):
    import knowledge
    return knowledge.forget(fact_contains)


def memories():
    import knowledge
    return knowledge.facts()


def update_profile(field, value, remove=False):
    import knowledge
    return knowledge.update_profile(field, value, remove in (True, "true"))


def learn_lesson(situation, lesson, kind="mistake"):
    import knowledge
    return knowledge.learn_lesson(situation, lesson, kind)


def recall_knowledge(about=""):
    import knowledge
    return knowledge.recall(about)


# ---------------------------------------------------------------- reactions and dragging windows (3D Nova)

def react(reaction, times=0):
    """Make 3D Nova act something out on screen."""
    import avatar_client
    name = str(reaction or "").lower().strip()
    name = {"laughing": "laugh", "dancing": "dance", "twirl": "spin", "applaud": "clap", "cheer": "happy", "jump": "happy",
            "cry": "sad", "shrug": "confused", "yes": "nod", "no": "shake", "oops": "facepalm", "thanks": "bow",
            "heart": "love", "stretch": "yawn", "hello": "wave", "hi": "wave"}.get(name, name)
    if name not in REACTIONS:
        return f"I can't do '{reaction}'. I can: {', '.join(REACTIONS)}."
    if not avatar_client.running():
        return "My 3D body isn't on screen right now, so I can't act it out."
    out = avatar_client.send("react", name=name, repeat=int(times) if times else None, wait=True, timeout=20)
    return f"Did it: {name}." if out and out.get("played") else f"I couldn't do '{name}' just now."


def _avatar_pos():
    import avatar_client
    return avatar_client.position()


def drag_window(window="", to="center"):
    """Move a window to another part of the screen. 3D Nova walks over, takes hold of its title bar and carries it."""
    import avatar_client
    w = _target_window(window)
    if not w:
        return f"No open window matches '{window}'."
    hwnd, title, _ = w
    if _user32.IsIconic(hwnd) or _user32.IsZoomed(hwnd):
        _user32.ShowWindow(hwnd, 9)             # a maximized or minimized window can't be moved: restore it first
        time.sleep(0.4)
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    area = wintypes.RECT(*work_area())
    ww, wh = r.right - r.left, r.bottom - r.top
    aw, ah = area.right - area.left, area.bottom - area.top
    ww, wh = min(ww, aw), min(wh, ah)
    spot = str(to or "center").lower().replace("_", "-").replace(" ", "-")
    xs = {"left": area.left, "right": area.right - ww, "center": area.left + (aw - ww) // 2}
    ys = {"top": area.top, "bottom": area.bottom - wh, "center": area.top + (ah - wh) // 2}
    places = {"left": ("left", "center"), "right": ("right", "center"), "center": ("center", "center"), "middle": ("center", "center"),
              "top": ("center", "top"), "bottom": ("center", "bottom"), "top-left": ("left", "top"), "top-right": ("right", "top"),
              "bottom-left": ("left", "bottom"), "bottom-right": ("right", "bottom")}
    if spot not in places:
        return f"I don't know the spot '{to}'. Use one of: {', '.join(places)}."
    nx, ny = xs[places[spot][0]], ys[places[spot][1]]
    ox, oy = r.left, r.top
    if abs(nx - ox) < 8 and abs(ny - oy) < 8:
        return f"'{title[:60]}' is already at the {spot}."

    def place(k):                               # k = 0 (where it was) .. 1 (where it is going)
        _user32.SetWindowPos(hwnd, 0, int(ox + (nx - ox) * k), int(oy + (ny - oy) * k), 0, 0, 0x0001 | 0x0004 | 0x0010)

    _focus(hwnd)
    start = _avatar_pos() if avatar_client.running() else None
    if start:
        avatar_client.send("say", text=f"Moving “{title[:30]}”…", secs=4)
        avatar_client.send("go_point", x=r.left + ww / 2, y=r.top + 16, timeout=15)     # walk to the title bar, take hold
        start = _avatar_pos() or start
        done = threading.Event()
        threading.Thread(target=lambda: (avatar_client.send("carry_to", x=start[0] + nx - ox, y=start[1] + ny - oy, timeout=20),
                                         done.set()), daemon=True).start()
        end_x, end_y = start[0] + nx - ox, start[1] + ny - oy
        total = max(1.0, ((end_x - start[0]) ** 2 + (end_y - start[1]) ** 2) ** 0.5)
        t0, moved = time.time(), 0.0
        while not done.is_set() and time.time() - t0 < 20:
            pos = _avatar_pos()
            if pos:      # the window follows her: as far along its path as she is along hers
                moved = max(moved, min(1.0, ((pos[0] - start[0]) ** 2 + (pos[1] - start[1]) ** 2) ** 0.5 / total))
                place(moved)
            time.sleep(0.03)
        for i in range(1, 9):                   # she may stop early at a screen edge: glide the rest of the way
            place(moved + (1 - moved) * i / 8)
            time.sleep(0.02)
        avatar_client.send("react", name="happy")
    else:
        for i in range(1, 16):
            place(i / 15)
            time.sleep(0.02)
    place(1)
    return f"Moved '{title[:60]}' to the {spot} of the screen."


# ---------------------------------------------------------------- registry

def _tool(name, desc, props=None, required=None):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": props or {}, "required": required or [],
                             "additionalProperties": False}}


S = {"type": "string"}
N = {"type": "number"}
B = {"type": "boolean"}

TOOLS = [
    _tool("get_system_status", "CPU, RAM, disk, battery and uptime of this laptop."),
    _tool("list_top_processes", "Programs using the most memory or CPU.",
          {"sort_by": {"type": "string", "enum": ["memory", "cpu"]}, "limit": N}),
    _tool("kill_process", "Close a running program by name (e.g. chrome) or pid.", {"name_or_pid": S}, ["name_or_pid"]),
    _tool("network_info", "Internet status, local IP and Wi-Fi details."),
    _tool("get_datetime", "Current local date and time."),
    _tool("open_app", "Open an app, e.g. notepad, calculator, chrome, settings, vscode, spotify, task manager.", {"name": S}, ["name"]),
    _tool("open_website", "Open a URL, or search Google if given words.", {"url_or_search": S}, ["url_or_search"]),
    _tool("open_path", "Open a file or folder. Shortcuts: desktop, downloads, documents, pictures, music, videos.", {"path": S}, ["path"]),
    _tool("list_folder", "List a folder's contents, newest first.", {"path": S}, ["path"]),
    _tool("search_files", "Find files/folders whose name contains text.", {"name_contains": S, "folder": S}, ["name_contains"]),
    _tool("find_large_files", "Find big files that take up space.", {"folder": S, "min_mb": N}),
    _tool("preview_organize_folder", "Show how a folder's loose files would be sorted into category folders (no changes).", {"folder": S}),
    _tool("organize_folder", "Sort a folder's loose files into Images/Documents/Videos/... subfolders.", {"folder": S}),
    _tool("create_folder", "Create a folder.", {"path": S}, ["path"]),
    _tool("delete_to_recycle_bin", "Move a file or folder to the Recycle Bin.", {"path": S}, ["path"]),
    _tool("temp_files_size", "How much space temporary files use."),
    _tool("clean_temp_files", "Delete old temporary files to free space."),
    _tool("empty_recycle_bin", "Permanently empty the Recycle Bin."),
    _tool("set_volume", "Change system volume.", {"action": {"type": "string", "enum": ["up", "down", "mute", "unmute"]}, "steps": N}, ["action"]),
    _tool("media_control", "Play/pause, next or previous track.", {"action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop"]}}, ["action"]),
    _tool("set_brightness", "Set screen brightness 0-100.", {"percent": N}, ["percent"]),
    _tool("lock_screen", "Lock the computer."),
    _tool("power_action", "Shutdown, restart, sleep, or cancel a pending shutdown.",
          {"action": {"type": "string", "enum": ["shutdown", "restart", "sleep", "cancel"]}}, ["action"]),
    _tool("run_powershell", "Run any PowerShell command for anything the other tools can't do. as_admin=true runs it elevated (Windows shows a UAC prompt).",
          {"command": S, "as_admin": B}, ["command"]),
    _tool("list_windows", "List open windows and their apps."),
    _tool("window_action", "Focus, minimize, maximize or close an open window (match by title or app name).",
          {"title_or_app": S, "action": {"type": "string", "enum": ["focus", "minimize", "maximize", "close"]}}, ["title_or_app", "action"]),
    _tool("minimize_all_windows", "Show the desktop."),
    _tool("type_text", "Type text with the keyboard, optionally into a window (focuses it first).", {"text": S, "into_window": S}, ["text"]),
    _tool("press_keys", "Press a key or shortcut, e.g. 'ctrl+s', 'alt+tab', 'enter', 'windows+e'.", {"keys": S}, ["keys"]),
    _tool("get_clipboard", "Read the clipboard text."),
    _tool("set_clipboard", "Put text on the clipboard.", {"text": S}, ["text"]),
    _tool("take_screenshot", "Save a screenshot to Pictures/Nova Screenshots."),
    _tool("read_text_file", "Read a text file (txt, md, csv, code, logs).", {"path": S, "max_chars": N}, ["path"]),
    _tool("write_text_file", "Create/overwrite or append to a text file.", {"path": S, "content": S, "append": B}, ["path", "content"]),
    _tool("move_or_rename", "Move or rename a file or folder.", {"source": S, "destination": S}, ["source", "destination"]),
    _tool("copy_path", "Copy a file or folder.", {"source": S, "destination": S}, ["source", "destination"]),
    _tool("search_installable_apps", "Search winget for apps that can be installed.", {"query": S}, ["query"]),
    _tool("install_app", "Install an app with winget by its exact Id (find it with search_installable_apps).", {"winget_id": S}, ["winget_id"]),
    _tool("uninstall_app", "Uninstall an app with winget.", {"name_or_id": S}, ["name_or_id"]),
    _tool("list_installed_apps", "List installed programs, optionally filtered.", {"name_contains": S}),
    _tool("list_startup_apps", "Programs that start with Windows."),
    _tool("open_settings_page", "Open a Windows Settings page: " + ", ".join(SETTINGS_PAGES), {"page": S}, ["page"]),
    _tool("set_dark_mode", "Turn Windows dark mode on or off.", {"enabled": B}, ["enabled"]),
    _tool("set_wallpaper", "Set the desktop wallpaper from an image file.", {"image_path": S}, ["image_path"]),
    _tool("toggle_wifi", "Turn the Wi-Fi adapter on or off (needs admin approval).", {"on": B}, ["on"]),
    _tool("list_clickable", "List buttons, links, tabs and text boxes in a window (default: the window in front), numbered "
          "in page order. 'contains' filters by text; contains='video' lists only videos (YouTube etc.).",
          {"window": S, "contains": S}),
    _tool("fill_in", "Type into a text box in a window and press Enter - use this to SEARCH on a website (YouTube, Google, "
          "Amazon...) or fill a form field. 'box' is the box's name; leave it empty for the page's search box.",
          {"text": S, "box": S, "window": S, "press_enter": B}, ["text"]),
    _tool("video_control", "Control a playing video (YouTube etc.) with its own player buttons: make the VIDEO full screen "
          "(not the browser window), exit full screen, play, pause, next video, mute, unmute, captions, theater mode.",
          {"action": {"type": "string", "enum": list(VIDEO_BUTTONS)}}, ["action"]),
    _tool("click_nth", "Click the Nth item (1 = first) whose name contains some text, scrolling to it if needed. "
          "To play a video: contains='video' (e.g. 'play the first video' -> number=1, 'the 7th video' -> number=7).",
          {"contains": S, "number": N, "window": S}, ["contains", "number"]),
    _tool("scroll", "Scroll a window up or down with the mouse wheel (amount 5 ≈ half a page).",
          {"direction": {"type": "string", "enum": ["up", "down"]}, "amount": N, "window": S}, ["direction"]),
    _tool("click_element", "Click a button/menu/link/tab/text box by its name in a window. Nova walks over and presses it. "
          "Call list_clickable first if you don't know the exact name.",
          {"name": S, "window": S, "button": {"type": "string", "enum": ["left", "right"]}, "double": B}, ["name"]),
    _tool("look_through_camera", "Look at the user through the webcam and answer a question about what you see "
          "(e.g. 'how do I look?', 'what am I holding?', 'do I look tired?'). Only works if the user switched the camera on.",
          {"question": S}, ["question"]),
    _tool("look_at_screen", "Look at the user's screen (or one window) and answer a question about it - read an error, "
          "explain what's open, summarize a page, find something. Only works if the user switched screen viewing on.",
          {"question": S, "what": {"type": "string", "enum": ["screen", "window"]}, "window": S}, ["question"]),
    _tool("click_at", "Click at screen coordinates (pixels).",
          {"x": N, "y": N, "button": {"type": "string", "enum": ["left", "right"]}, "double": B}, ["x", "y"]),
    _tool("add_note", "Save a note.", {"text": S}, ["text"]),
    _tool("list_notes", "Read saved notes."),
    _tool("set_reminder", "Remind the user after some minutes.", {"text": S, "minutes_from_now": N}, ["text", "minutes_from_now"]),
    _tool("remember_fact", "Remember a lasting fact about the user that fits no profile field.", {"fact": S}, ["fact"]),
    _tool("forget_fact", "Forget everything in your knowledge bank (profile or lessons) containing some text.",
          {"fact_contains": S}, ["fact_contains"]),
    _tool("update_profile", "Save something you learned about the user in your knowledge bank, so you know them better "
          "next time. Use it whenever they tell you about themselves.",
          {"field": {"type": "string", "enum": ["name", "nickname", "work", "city", "interests", "favourite_apps",
                                                "preferences", "routines", "people", "facts"]},
           "value": S, "remove": B}, ["field", "value"]),
    _tool("learn_lesson", "Write down a mistake you made (or a better way you found) and what to do next time. Use it "
          "when the user corrects you, when a task failed, or when you discover what works on this laptop.",
          {"situation": S, "lesson": S, "kind": {"type": "string", "enum": ["mistake", "improvement", "preference"]}},
          ["situation", "lesson"]),
    _tool("recall_knowledge", "Read your knowledge bank: what you know about the user and your lessons. Optionally only "
          "about one topic.", {"about": S}),
    _tool("react", "Act something out with your 3D body on screen: laugh, dance, spin, clap, surprised, sad, confused, nod, "
          "shake (no), facepalm (oops), bow (thanks), love, yawn, look, happy (jump), wave. Use it when the user asks "
          "you to dance, laugh, clap and so on.",
          {"reaction": {"type": "string", "enum": list(REACTIONS)}, "times": N}, ["reaction"]),
    _tool("drag_window", "Move a window to another part of the screen - you walk over, grab its title bar and carry it.",
          {"window": S, "to": {"type": "string", "enum": ["left", "right", "center", "top", "bottom", "top-left",
                                                          "top-right", "bottom-left", "bottom-right"]}}, ["to"]),
]

# Approval tiers, used by the autonomy setting in app.py:
#   careful -> asks before RISKY and DANGEROUS;  trusted -> asks only before DANGEROUS;  full -> never asks.
RISKY = {"kill_process", "organize_folder", "delete_to_recycle_bin", "clean_temp_files", "run_powershell",
         "window_action", "type_text", "press_keys", "write_text_file", "move_or_rename", "install_app",
         "set_wallpaper", "power_action", "empty_recycle_bin", "uninstall_app", "toggle_wifi",
         "click_element", "click_at", "click_nth", "fill_in"}
DANGEROUS = {"empty_recycle_bin", "uninstall_app", "toggle_wifi"}  # plus shutdown/restart and admin PowerShell


def is_dangerous(name, args):
    if name in DANGEROUS:
        return True
    if name == "power_action" and args.get("action") in ("shutdown", "restart"):
        return True
    return name == "run_powershell" and args.get("as_admin") in (True, "true")

FUNCS = {t["name"]: globals()[t["name"]] for t in TOOLS}


def run_tool(name, args):
    if name not in FUNCS:
        return f"Error in {name}: no such tool."
    try:
        import inspect
        allowed = inspect.signature(FUNCS[name]).parameters
        args = {k: v for k, v in (args or {}).items() if k in allowed}  # local models sometimes add extras
        return str(FUNCS[name](**args))
    except Exception as e:
        return f"Error in {name}: {e}"
