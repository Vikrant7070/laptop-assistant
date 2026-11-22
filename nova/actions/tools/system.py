"""System health, programs, network, volume / brightness / media / power, and Windows settings pages."""


import datetime as dt
import os
import platform
import re
import socket
import subprocess
import time
from pathlib import Path

import psutil

from nova import platform as nova_platform     # (the stdlib `platform` is imported above)
from nova.actions.registry import tool
from nova.shared import ps_escape
from nova.actions.tools import common, windows


def stats():
    """Small dict for the live dashboard."""
    bat = psutil.sensors_battery()
    disk = psutil.disk_usage(str(common.HOME.anchor))
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "ram": psutil.virtual_memory().percent,
        "disk": disk.percent,
        "battery": round(bat.percent) if bat else None,
        "plugged": bat.power_plugged if bat else None,
        "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1),
    }


@tool("CPU, RAM, disk, battery and uptime of this laptop.", friendly="Checked system health", core=True,
      hints="battery charge cpu ram memory disk", props={}, required=[])
def get_system_status():
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(common.HOME.anchor))
    bat = psutil.sensors_battery()
    lines = [
        f"Computer: {platform.node()} ({platform.system()} {platform.release()})",
        f"CPU: {psutil.cpu_percent(interval=0.5)}% used, {psutil.cpu_count()} logical cores",
        f"RAM: {vm.percent}% used ({common._fmt_bytes(vm.used)} of {common._fmt_bytes(vm.total)})",
        f"Disk {common.HOME.anchor}: {disk.percent}% used, {common._fmt_bytes(disk.free)} free of {common._fmt_bytes(disk.total)}",
        f"Uptime: {round((time.time() - psutil.boot_time()) / 3600, 1)} hours",
    ]
    if bat:
        mins = bat.secsleft // 60 if bat.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) else None
        lines.append(f"Battery: {round(bat.percent)}%, {'charging' if bat.power_plugged else 'on battery'}"
                     + (f", about {mins} min left" if mins and not bat.power_plugged else ""))
    return "\n".join(lines)


@tool("Programs using the most memory or CPU.", friendly="Looked at running programs",
      hints="what is slowing my laptop, using memory, hogging cpu",
      props={"sort_by": {"type": "string", "enum": ["memory", "cpu"]}, "limit": common.N}, required=[])
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
    rows = [f"{p['name']} (pid {p['pid']}): {common._fmt_bytes(p['memory_info'].rss) if p['memory_info'] else '?'} RAM, {p['cpu_percent'] or 0}% CPU"
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


@tool("Close a running program by name (e.g. chrome) or pid.", friendly="Closed a program", risky="Close a program",
      hints="close force quit end task kill app", props={"name_or_pid": common.S}, required=["name_or_pid"])
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


@tool("Internet status, local IP and Wi-Fi details.", friendly="Checked the network",
      hints="internet wifi connected ip speed", props={}, required=[])
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
    wifi = common._ps("netsh wlan show interfaces | Select-String -Pattern ' SSID|Signal|State' | ForEach-Object { $_.ToString().Trim() }")
    return f"Internet: {online}\nLocal IP: {ip}\n{wifi}"


@tool("Current local date and time.", friendly="Checked the time", core=True, props={}, required=[])
def get_datetime():
    return dt.datetime.now().strftime("%A, %d %B %Y, %I:%M %p")


@tool("Change system volume.", friendly="Adjusted volume",
      hints="volume louder quieter sound mute unmute turn it up down",
      props={"action": {"type": "string", "enum": ["up", "down", "mute", "unmute"]}, "steps": common.N}, required=["action"])
def set_volume(action, steps=5):
    keys = {"up": 175, "down": 174, "mute": 173, "unmute": 173}
    if action not in keys:
        return "action must be up, down, mute or unmute"
    n = 1 if action in ("mute", "unmute") else int(steps)
    common._ps(f"$w = New-Object -ComObject WScript.Shell; 1..{n} | ForEach-Object {{ $w.SendKeys([char]{keys[action]}) }}")
    return f"Volume {action}" + (f" by {n} steps." if n > 1 else ".")


@tool("Play/pause, next or previous track.", friendly="Controlled media",
      hints="play pause next song previous track music spotify",
      props={"action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop"]}}, required=["action"])
def media_control(action):
    keys = {"play_pause": 179, "next": 176, "previous": 177, "stop": 178}
    if action not in keys:
        return "action must be play_pause, next, previous or stop"
    common._ps(f"(New-Object -ComObject WScript.Shell).SendKeys([char]{keys[action]})")
    return f"Media: {action.replace('_', '/')}."


@tool("Set screen brightness 0-100.", friendly="Adjusted brightness", hints="brightness screen dimmer brighter",
      props={"percent": common.N}, required=["percent"])
def set_brightness(percent):
    pct = max(0, min(100, int(percent)))
    out = common._ps(f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{pct})")
    return f"Brightness set to {pct}%." if "ERR" not in out else f"Couldn't change brightness here: {out}"


@tool("Lock the computer.", friendly="Locked the screen", hints="lock my laptop", props={}, required=[])
def lock_screen():
    subprocess.Popen("rundll32.exe user32.dll,LockWorkStation")
    return "Locked the screen."


@tool("Shutdown, restart, sleep, or cancel a pending shutdown. Shutdown/restart first closes "
      "all open windows and clears notifications.", friendly="Power action", risky="Change power state",
      hints="shut down turn off restart sleep the laptop computer pc",
      props={"action": {"type": "string", "enum": ["shutdown", "restart", "sleep", "cancel"]}}, required=["action"])
def power_action(action):
    cmds = {"shutdown": "shutdown /s /t 30", "restart": "shutdown /r /t 30",
            "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0", "cancel": "shutdown /a"}
    if action not in cmds:
        return "action must be shutdown, restart, sleep or cancel"
    tidy = ""
    if action in ("shutdown", "restart"):
        # Leave a clean slate: close every open window (apps still get to ask about unsaved work) and clear the
        # notifications waiting in the notification centre, then start the countdown.
        tidy = windows.close_all_windows() + " " + _clear_notifications() + " "
    subprocess.Popen(cmds[action], shell=True)
    if action in ("shutdown", "restart"):
        return f"{tidy}{action.title()} in 30 seconds. Say 'cancel shutdown' to stop it."
    return "Done." if action == "cancel" else "Going to sleep."


def _clear_notifications():
    try:
        from nova.actions import notifications
        notifications.clear_all()
        return "Cleared your notifications."
    except Exception as e:
        return f"(Couldn't clear notifications: {e})"


# The user wants to WATCH Nova work, so hidden PowerShell is turned back when an on-screen tool can do the job.
# (Measured: 77 of her recent tool calls were hidden PowerShell - mostly listing/reading files and git.)
# no_ui_way = her reason why no on-screen way works; then it runs. Admin commands are system jobs: never turned back.
UI_WAYS = [
    (r"^\s*(git|gh)\b|[;|&]\s*(git|gh)\b",
     "git/GitHub: do it in VS Code (Source Control panel; the vscode-git-push skill pushes), or for a git command "
     "with no button, run_in_terminal so the user sees it typed and its output"),
    (r"^\s*(get-childitem|gci|ls|dir)\b",
     "listing a folder: list_folder, and open_path the folder so the user sees it in File Explorer"),
    (r"^\s*(get-content|gc|cat|type|select-string)\b",
     "reading a file: read_text_file, and open_path to show the file to the user"),
    (r"\b(stop-process|taskkill)\b|closemainwindow|\.kill\(\)",
     "closing apps: window_action close or close_all_windows (you walk over and click X); kill_process only "
     "for an app that hangs"),
    (r"^\s*(start-process|start|invoke-item|ii|explorer(\.exe)?)\b(?!.*-verb\s+runas)",
     "opening things: open_app, open_website or open_path"),
    (r"^\s*(new-item\b.*-itemtype\s+directory|mkdir|md)\b", "making a folder: create_folder"),
    (r"^\s*(remove-item|rm|del|erase|rmdir|rd)\b", "deleting: delete_to_recycle_bin"),
    (r"^\s*(move-item|rename-item|mv|move|ren|rename)\b", "moving or renaming: move_or_rename"),
    (r"mainwindowtitle", "seeing which windows are open: list_windows"),
    (r"^\s*(set-clipboard|get-clipboard)\b", "the clipboard: get_clipboard / set_clipboard"),
    (r"^\s*(npm|npx|pip|python|py|node|ssh|scp|winget|choco|docker|code)\b",
     "a command-line program: run_in_terminal, so the user watches you type it and sees the output"),
]


def ui_way(command):
    """The on-screen way to do what this PowerShell command does, or None if it is a real system job."""
    for pattern, hint in UI_WAYS:
        if re.search(pattern, command or "", re.I):
            return hint
    return None


@tool("Last resort, HIDDEN from the user: a PowerShell command for system jobs no app or other "
      "tool can do (system settings and info). The user wants to watch you work, so commands a visible tool "
      "can do (listing/reading files, opening/closing apps, git, command-line programs) come back not run, with "
      "the tool to use. NOT for web research (web_research/read_page), NOT for git/coding (VS Code). "
      "as_admin=true runs it elevated (Windows shows a UAC prompt).", friendly="Ran a command",
      risky="Run a PowerShell command",
      hints="powershell command, windows version, os build number, hotfixes, bios, uptime, registry value",
      props={"command": common.S, "as_admin": common.B,
      "no_ui_way": {"type": "string", "description": "only after a 'Not run' answer: why no on-screen way works"}},
      required=["command"])
def run_powershell(command, as_admin=False, no_ui_way=""):
    if not as_admin:
        hint = ui_way(command)
        if hint and not str(no_ui_way or "").strip():
            return (f"Not run - the user wants to watch you work on screen, so use the on-screen way for {hint}. "
                    "Only if none of those can do it, call run_powershell again with no_ui_way saying why.")
        return common._ps(command, timeout=120)
    # Elevated: Windows shows its own UAC prompt; output comes back through a temp file.
    out = Path(os.environ.get("TEMP", ".")) / f"nova_admin_{int(time.time())}.txt"
    script = out.with_suffix(".ps1")
    script.write_text(f"& {{ {command} }} *> '{ps_escape(out)}'", encoding="utf-8")
    common._ps(f"Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
        f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"{ps_escape(script)}\"'", timeout=300)
    try:
        raw = out.read_bytes() if out.exists() else b""
        result = raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8", errors="ignore")
    finally:
        script.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
    return (result.strip()[:6000] or "(ran as administrator, no output - or the UAC prompt was declined)")


@tool("Search winget for apps that can be installed.", friendly="Searched for apps", props={"query": common.S},
      required=["query"])
def search_installable_apps(query):
    return common._ps(f"winget search --accept-source-agreements -- '{ps_escape(query)}' | Select-Object -First 15", timeout=90)


@tool("Install an app with winget by its exact Id (find it with search_installable_apps).",
      friendly="Installed an app", risky="Install an app", hints="install download an app program",
      props={"winget_id": common.S}, required=["winget_id"])
def install_app(winget_id):
    return common._ps(f"winget install --id '{ps_escape(winget_id)}' -e --silent --accept-package-agreements "
               "--accept-source-agreements", timeout=900)


@tool("Uninstall an app with winget.", friendly="Uninstalled an app", risky="Uninstall an app", dangerous=True,
      hints="uninstall remove an app program", props={"name_or_id": common.S}, required=["name_or_id"])
def uninstall_app(name_or_id):
    return common._ps(f"winget uninstall --silent --accept-source-agreements -- '{ps_escape(name_or_id)}'", timeout=600)


def _like_literal(text):
    """Wildcard characters in the user's text are matched literally by -like."""
    return re.sub(r"([*?\[\]`])", r"`\1", str(text or ""))


@tool("List installed programs, optionally filtered.", friendly="Listed installed apps", props={"name_contains": common.S},
      required=[])
def list_installed_apps(name_contains=""):
    cmd = ("Get-ItemProperty HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*,"
           "HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*,"
           "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\* -ErrorAction SilentlyContinue | "
           f"Where-Object {{ $_.DisplayName -like '*{ps_escape(_like_literal(name_contains))}*' }} | Sort-Object DisplayName -Unique | "
           "Select-Object -First 60 -ExpandProperty DisplayName")
    return common._ps(cmd)


SETTINGS_PAGES = {
    "wifi": "network-wifi", "bluetooth": "bluetooth", "display": "display", "sound": "sound",
    "battery": "batterysaver", "power": "powersleep", "updates": "windowsupdate", "apps": "appsfeatures",
    "startup": "startupapps", "storage": "storagesense", "privacy": "privacy", "personalization": "personalization",
    "background": "personalization-background", "notifications": "notifications", "mouse": "mousetouchpad",
    "keyboard": "typing", "time": "dateandtime", "accounts": "yourinfo", "default apps": "defaultapps",
    "night light": "nightlight", "vpn": "network-vpn", "hotspot": "network-mobilehotspot",
}


@tool("Open a Windows Settings page: " + ", ".join(SETTINGS_PAGES), friendly="Opened Settings", props={"page": common.S},
      required=["page"])
def open_settings_page(page):
    key = SETTINGS_PAGES.get(page.lower().strip(), page.lower().strip())
    os.startfile(f"ms-settings:{key}")
    return f"Opened Settings > {page}."


@tool("Turn Windows dark mode on or off.", friendly="Changed theme", hints="dark mode light mode theme",
      props={"enabled": common.B}, required=["enabled"])
def set_dark_mode(enabled):
    on = enabled in (True, "true", "on", 1)
    nova_platform.current().set_dark_mode(on)
    return f"Dark mode {'on' if on else 'off'}."


@tool("Set the desktop wallpaper from an image file.", friendly="Changed wallpaper", risky="Change your wallpaper",
      props={"image_path": common.S}, required=["image_path"])
def set_wallpaper(image_path):
    p = common._resolve(image_path)
    if not p.is_file():
        return f"{p} is not an image file."
    nova_platform.current().set_wallpaper(p)
    return f"Wallpaper set to {p.name}."
