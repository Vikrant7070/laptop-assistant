"""Keeping the laptop fast: how full memory really is, and which apps start with Windows.

check_memory looks at RAM *and* the pagefile (the part of the SSD Windows uses as extra memory). When the two together
("commit") are nearly full, the laptop slows down and apps can crash - a bigger pagefile doesn't make it faster, closing
programs does. list_startup_apps / set_startup_app read and flip the same on/off switch as Settings > Apps > Startup and
Task Manager (the StartupApproved registry keys), so anything turned off can be turned back on there.
"""
import os
import struct
import time
import winreg
from pathlib import Path

import psutil

from nova import platform
from nova.actions.registry import tool
from nova.log import note

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
USER_FOLDER = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup"
COMMON_FOLDER = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs\StartUp"

# Startup entries nobody needs at login: updaters and launchers that start fine on their own when opened.
NOT_NEEDED = ("updater", "update", "download assistant", "steam", "edgeautolaunch", "microsoft.lists", "spotify",
              "discord", "epicgames", "epic games", "skype", "cortana", "teams", "adobe arm", "ccleaner")
# Never turned off by Nova: Windows Security, Nova herself, drivers and sound/graphics helpers.
PROTECTED = ("securityhealth", "windows defender", "nova", "realtek", "nvidia", "intel", "synaptics", "elan",
             "audio", "igfx", "ctfmon")


def _gb(n):
    return f"{n / 1024 ** 3:.1f} GB"




def _commit():
    """(used, limit) in bytes: RAM + pagefile that programs have claimed, and the most they can claim."""
    return platform.current().memory_commit()


def _top_programs(limit=6):
    """Memory per program name (Chrome's 12 processes count as one Chrome)."""
    totals, counts = {}, {}
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            name = (p.info["name"] or "?").removesuffix(".exe")
            totals[name] = totals.get(name, 0) + (p.info["memory_info"].rss if p.info["memory_info"] else 0)
            counts[name] = counts.get(name, 0) + 1
        except psutil.Error:
            pass
    top = sorted(totals, key=totals.get, reverse=True)
    top = [n for n in top if n not in ("System", "Memory Compression", "MemCompression", "Registry", "svchost", "Idle")][:limit]
    return [(n, counts[n], totals[n]) for n in top]


@tool("How full the laptop's memory really is: RAM, the pagefile (SSD used as extra memory) and which programs use "
      "the most. Use when the laptop feels slow or the user asks about RAM or memory.",
      friendly="Checked the memory", hints="laptop slow, ram full, memory, virtual memory, pagefile, speed up laptop",
      group=("list_top_processes", "kill_process"))
def check_memory():
    vm = psutil.virtual_memory()
    lines = [f"RAM: {vm.percent}% used, {_gb(vm.available)} free of {_gb(vm.total)}"]
    c = _commit()
    if c:
        used, limit = c
        pct = round(100 * used / limit) if limit else 0
        lines.append(f"Memory in use including the pagefile (SSD): {_gb(used)} of {_gb(limit)} ({pct}%)")
        if pct >= 90:
            lines.append("That is nearly full: the laptop will lag and apps may crash. Closing big programs helps "
                         "far more than a bigger pagefile.")
        elif vm.percent >= 85:
            lines.append("RAM is busy, so Windows is leaning on the slower SSD - closing big programs will speed it up.")
    top = _top_programs()
    rows = [f"- {n}{f' ({k} windows/processes)' if k > 1 else ''}: {_gb(b)}" for n, k, b in top]
    note("SPEEDUP memory: " + " | ".join(lines[:2]) + " | top: " + ", ".join(f"{n} {_gb(b)}" for n, _, b in top))
    return "\n".join(lines + ["Biggest programs:"] + rows)


# ---------------------------------------------------------------- startup apps

def _approved_on(hive, sub, value):
    """True / False from the StartupApproved switch; True when Windows has no entry (it starts)."""
    try:
        with winreg.OpenKey(hive, f"{APPROVED}\\{sub}") as k:
            data, _ = winreg.QueryValueEx(k, value)
        return not (bytes(data)[:1] and bytes(data)[0] & 1)    # 2/6 = on, 3/7 = off
    except OSError:
        return True


def _entries():
    """Every app that starts with Windows: dicts with name, hive, sub (StartupApproved subkey), value, where, on."""
    out = []
    for hive, where in ((winreg.HKEY_CURRENT_USER, "you"), (winreg.HKEY_LOCAL_MACHINE, "all users")):
        try:
            with winreg.OpenKey(hive, RUN) as k:
                i = 0
                while True:
                    try:
                        name, _, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    out.append({"name": name, "hive": hive, "sub": "Run", "value": name, "where": where})
        except OSError:
            pass
    for hive, folder, where in ((winreg.HKEY_CURRENT_USER, USER_FOLDER, "you"),
                                (winreg.HKEY_LOCAL_MACHINE, COMMON_FOLDER, "all users")):
        try:
            files = [f for f in folder.iterdir() if f.is_file() and f.name.lower() != "desktop.ini"]
        except OSError:
            files = []
        for f in files:
            out.append({"name": f.stem, "hive": hive, "sub": "StartupFolder", "value": f.name, "where": where})
    for e in out:
        e["on"] = _approved_on(e["hive"], e["sub"], e["value"])
    return out


def _protected(name):
    n = name.lower()
    return any(p in n for p in PROTECTED)


def _not_needed(name):
    n = name.lower().replace(" ", "")
    return not _protected(name) and any(p.replace(" ", "") in n for p in NOT_NEEDED)


@tool("List the apps that start with Windows, whether each is on or off, and which ones aren't needed at startup.",
      friendly="Looked at startup apps", hints="startup apps, programs that start with windows, boot slow, login slow",
      group=("set_startup_app",))
def list_startup_apps():
    rows = []
    for e in _entries():
        tag = " - not needed at startup" if e["on"] and _not_needed(e["name"]) else ""
        tag = " - keep (protected)" if _protected(e["name"]) else tag
        rows.append(f"- {e['name']}: {'ON' if e['on'] else 'off'}{tag}")
    return ("Apps that start with Windows:\n" + "\n".join(rows)) if rows else "No startup apps found."


def _write(e, on):
    data = bytes([2]) + bytes(11) if on else bytes([3, 0, 0, 0]) + struct.pack("<Q", int((time.time() + 11644473600) * 10 ** 7))
    with winreg.CreateKeyEx(e["hive"], f"{APPROVED}\\{e['sub']}", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, e["value"], 0, winreg.REG_BINARY, data)


@tool("Turn an app that starts with Windows on or off (the same switch as Settings > Apps > Startup; nothing is "
      "uninstalled). name='extras' turns off every app list_startup_apps marks as not needed.",
      friendly="Changed startup apps", risky="Change which apps start with Windows",
      props={"name": {"type": "string", "description": "the startup app's name, or 'extras' for all not-needed ones"},
             "enabled": {"type": "boolean", "description": "true = start with Windows, false = don't"}})
def set_startup_app(name, enabled=False):
    want = str(name).strip().lower()
    on = enabled in (True, "true", "True", 1)
    entries = _entries()
    if want == "extras":
        if on:
            return "name='extras' only turns apps off - name the app to turn one back on."
        targets = [e for e in entries if e["on"] and _not_needed(e["name"])]
        if not targets:
            note("SPEEDUP startup: extras -> nothing to turn off (" + ", ".join(
                f"{e['name']}={'on' if e['on'] else 'off'}" for e in entries) + ")")
            return "Nothing to turn off - every app that isn't needed at startup is already off."
    else:
        targets = [e for e in entries if e["name"].lower() == want] or \
                  [e for e in entries if want and want in e["name"].lower()]
        if not targets:
            note(f"SPEEDUP startup: no startup app matched {name!r}")
            return f"No startup app matched '{name}'. " + list_startup_apps()
    done, admin, kept = [], [], []
    note(f"SPEEDUP startup: turn {'on' if on else 'off'} name={name!r} -> {len(targets)} match(es): "
         + ", ".join(e["name"] for e in targets))
    for e in targets:
        where = f"{'HKCU' if e['hive'] == winreg.HKEY_CURRENT_USER else 'HKLM'}\\{APPROVED}\\{e['sub']}\\{e['value']}"
        if not on and _protected(e["name"]):
            kept.append(e["name"])
            note(f"SPEEDUP startup   kept (protected): {e['name']}")
            continue
        try:
            _write(e, on)
            done.append(e["name"])
            note(f"SPEEDUP startup   {'on' if on else 'off'}: {e['name']} ({where})")
        except PermissionError as ex:
            admin.append(e["name"])
            note(f"SPEEDUP startup   NEEDS ADMIN: {e['name']} ({where}): {ex}")
        except OSError as ex:
            admin.append(f"{e['name']} ({ex})")
            note(f"SPEEDUP startup   FAILED: {e['name']} ({where}): {type(ex).__name__}: {ex}")
    msg = []
    if done:
        msg.append(f"Turned {'on' if on else 'off'} at startup: " + ", ".join(done)
                   + (". They still open normally when you start them." if not on else "."))
    if admin:
        msg.append("These are set for all users and need admin rights - turn them off in Settings > Apps > Startup: "
                   + ", ".join(admin))
    if kept:
        msg.append("Left on because Windows or Nova needs them: " + ", ".join(kept))
    return " ".join(msg) or "Nothing changed."
