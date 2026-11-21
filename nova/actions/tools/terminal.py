"""A PowerShell terminal the user can watch Nova type into."""


import json
import os
import subprocess
import threading
import time
from pathlib import Path
import ctypes
from ctypes import wintypes

from nova import store
from nova.actions.registry import tool
from nova.shared import work_area
from nova.actions.tools import common, forms, windows


# For command-line jobs with no app or button (git log, ssh, npm, pip...): one visible PowerShell window titled
# "Nova terminal". Nova walks over and types the command in it like a person; the user sees the command and its
# output. Start-Transcript copies the session to a file and the prompt function counts finished commands, so she
# knows when it is done and can read what it printed.
TERM_TITLE = "Nova terminal"


TERM_WAIT = 120


# The visible terminal runs this loop: it waits for a command from Nova (cmd.json), types it out on screen, runs it,
# shows the output and saves it (out_<id>.txt), then marks it done (done_<id>.txt). Typing it out from inside avoids
# sending keystrokes (lost if the user clicks elsewhere) and captures what programs like git print - which
# PowerShell 5.1's transcript misses. The user can still type into it when a command asks a question.
TERM_SCRIPT = r"""
$Host.UI.RawUI.WindowTitle = 'Nova terminal'
$dir = '__DIR__'
Set-Location -LiteralPath '__HOME__'
Write-Host "Nova's terminal - she types her commands here so you can watch." -ForegroundColor Cyan
while ($true) {
  $req = Join-Path $dir 'cmd.json'
  if (Test-Path -LiteralPath $req) {
    $j = Get-Content -LiteralPath $req -Raw -Encoding UTF8 | ConvertFrom-Json
    Remove-Item -LiteralPath $req
    $out = Join-Path $dir ("out_" + $j.id + ".txt")
    if ($j.folder) { Set-Location -LiteralPath $j.folder }
    Write-Host ""
    Write-Host -NoNewline ("PS " + (Get-Location) + "> ") -ForegroundColor Gray
    foreach ($ch in $j.command.ToCharArray()) { Write-Host -NoNewline $ch -ForegroundColor Yellow; Start-Sleep -Milliseconds 22 }
    Write-Host ""
    $global:LASTEXITCODE = 0
    try {
      & ([scriptblock]::Create($j.command)) 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $out | Write-Host
    } catch {
      ("ERROR: " + $_.Exception.Message) | Tee-Object -FilePath $out -Append | Write-Host -ForegroundColor Red
    }
    Set-Content -LiteralPath (Join-Path $dir ("done_" + $j.id + ".txt")) -Value $global:LASTEXITCODE
  }
  Start-Sleep -Milliseconds 150
}
"""


def _terminal():
    return next(((h, t) for h, t, _ in windows._windows() if t == TERM_TITLE or t.endswith(" - " + TERM_TITLE)), None)


def _open_terminal():
    store.current().terminal.mkdir(parents=True, exist_ok=True)
    for old in store.current().terminal.glob("*.txt"):
        old.unlink(missing_ok=True)
    script = store.current().terminal / "nova_terminal.ps1"
    script.write_text(TERM_SCRIPT.replace("__DIR__", str(store.current().terminal).replace("'", "''"))
                      .replace("__HOME__", str(common.HOME).replace("'", "''")), encoding="utf-8-sig")
    si = subprocess.STARTUPINFO()                 # opens minimized and never takes the focus from what the user is doing
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 7                            # SW_SHOWMINNOACTIVE
    subprocess.Popen(["powershell", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                     creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si)
    end = time.time() + 10
    while time.time() < end:
        w = _terminal()
        if w:
            return w
        time.sleep(0.2)
    return None


def _read_text(path):
    raw = path.read_bytes() if path.exists() else b""
    enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    return raw.decode(enc, errors="ignore")


@tool("Run a command in your own 'Nova terminal' window and get what it printed. The window "
      "stays minimized in the background so the user can keep working; set show=true only if they ask to see or "
      "watch it. Use it for command-line jobs with no app or button (git log/status/remote, ssh, npm, pip, "
      "python scripts...).", friendly="Typed a command in the terminal", risky="Type a command in the terminal",
      core=True,
      hints="git commit log status branch remote diff last commit of my project repo, ssh npm pip python script "
            "command line terminal run a command",
      props={"command": common.S, "folder": {"type": "string", "description": "folder to run it in (e.g. the project folder)"},
      "show": common.B}, required=["command"])
def run_in_terminal(command, folder="", show=False):
    """Run a command in the Nova terminal and return what it printed. The terminal stays minimized in the taskbar
    so the user can keep working (it never takes the focus); show=True brings it up so they can watch."""
    from nova.avatar import avatar_client
    command = "; ".join(line.strip() for line in str(command or "").splitlines() if line.strip())
    if not command:
        return "Error in run_in_terminal: no command."
    where = str(Path(os.path.expandvars(folder)).expanduser()) if folder else ""
    if where and not Path(where).is_dir():
        return f"Error in run_in_terminal: no folder '{folder}'."
    w = _terminal() or _open_terminal()
    if not w:
        return "Error in run_in_terminal: the terminal window didn't open."
    hwnd = w[0]
    if show:                                      # only when the user asks to watch it
        if windows._user32.IsIconic(hwnd):
            windows._user32.ShowWindow(hwnd, 9)
        windows._focus(hwnd)
    job = f"{int(time.time() * 1000)}"
    out, done = store.current().terminal / f"out_{job}.txt", store.current().terminal / f"done_{job}.txt"
    if avatar_client.running():
        avatar_client.send("say", text="Running it in my terminal…", secs=3)
    tmp = store.current().terminal / "cmd.tmp"
    tmp.write_text(json.dumps({"id": job, "command": command, "folder": where}), encoding="utf-8")
    tmp.replace(store.current().terminal / "cmd.json")            # whole file at once: the loop never reads half a command
    end = time.time() + TERM_WAIT
    while time.time() < end and not done.exists():
        time.sleep(0.2)
    text = _read_text(out).strip()
    if not done.exists():
        return (f"Still running in the Nova terminal after {TERM_WAIT} s (the user can see it - it may be waiting "
                "for them to type something). So far:\n" + (text[-3000:] or "(nothing printed yet)"))
    code = _read_text(done).strip()
    out.unlink(missing_ok=True)
    done.unlink(missing_ok=True)
    return (text[-4000:] or "(done, no output)") + (f"\n(exit code {code})" if code not in ("", "0") else "")


def _avatar_pos():
    from nova.avatar import avatar_client
    return avatar_client.position()


@tool("Move a window to another part of the screen - you walk over, grab its title bar and carry it.",
      friendly="Moved a window", hints="move this window to the left right side",
      props={"window": common.S, "to": {"type": "string", "enum": ["left", "right", "center", "top", "bottom", "top-left",
      "top-right", "bottom-left", "bottom-right"]}}, required=["to"])
def drag_window(window="", to="center"):
    """Move a window to another part of the screen. 3D Nova walks over, takes hold of its title bar and carries it."""
    from nova.avatar import avatar_client
    w = forms._target_window(window)
    if not w:
        return f"No open window matches '{window}'."
    hwnd, title, _ = w
    if windows._user32.IsIconic(hwnd) or windows._user32.IsZoomed(hwnd):
        windows._user32.ShowWindow(hwnd, 9)             # a maximized or minimized window can't be moved: restore it first
        time.sleep(0.4)
    r = wintypes.RECT()
    windows._user32.GetWindowRect(hwnd, ctypes.byref(r))
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
        windows._user32.SetWindowPos(hwnd, 0, int(ox + (nx - ox) * k), int(oy + (ny - oy) * k), 0, 0, 0x0001 | 0x0004 | 0x0010)

    windows._focus(hwnd)
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
