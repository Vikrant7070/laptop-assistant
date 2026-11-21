"""Nova reads your Windows notifications (only when you switch it on: ⚙ Model → Notifications).

Windows' own notification listener (WinRT UserNotificationListener) is used through PowerShell, like the speech
recognizer in voice.py - no extra packages. It needs "Let apps access your notifications" to be allowed in Windows
Settings → Privacy & security → Notifications (it is by default).

  latest(n)   - the newest notifications, for "read my notifications"
  Watcher     - one long-running PowerShell loop that prints each NEW notification as a JSON line; Nova reads them
                out as they arrive. Notifications already there when it starts are not read out.
Nothing is stored on disk; the text only goes to Nova's voice and chat (and to her brain when you ask about them).
"""
import json
import subprocess
import threading
import time

_HEAD = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.UI.Notifications.Management.UserNotificationListener, Windows.UI.Notifications, ContentType = WindowsRuntime]
$null = [Windows.UI.Notifications.UserNotification, Windows.UI.Notifications, ContentType = WindowsRuntime]
$null = [Windows.UI.Notifications.KnownNotificationBindings, Windows.UI.Notifications, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $null = $task.Wait(10000); $task.Result }
$listener = [Windows.UI.Notifications.Management.UserNotificationListener]::Current
$access = $listener.GetAccessStatus()
if ($access -ne 'Allowed') { $access = Await ($listener.RequestAccessAsync()) ([Windows.UI.Notifications.Management.UserNotificationListenerAccessStatus]) }
if ($access -ne 'Allowed') { Write-Output (@{error = "access $access"} | ConvertTo-Json -Compress); exit }
function Get-Toasts {
  Await ($listener.GetNotificationsAsync([Windows.UI.Notifications.NotificationKinds]::Toast)) ([System.Collections.Generic.IReadOnlyList[Windows.UI.Notifications.UserNotification]])
}
function As-Json($n) {
  $app = try { $n.AppInfo.DisplayInfo.DisplayName } catch { '' }
  $b = $n.Notification.Visual.GetBinding([Windows.UI.Notifications.KnownNotificationBindings]::ToastGeneric)
  $texts = if ($b) { @($b.GetTextElements() | ForEach-Object { $_.Text }) } else { @() }
  @{id = $n.Id; app = $app; texts = $texts; time = $n.CreationTime.ToUnixTimeSeconds()} | ConvertTo-Json -Compress
}
"""

LATEST = _HEAD + r"""
$all = @(Get-Toasts) | Sort-Object { $_.CreationTime } -Descending | Select-Object -First __N__
foreach ($n in $all) { Write-Output (As-Json $n) }
"""

WATCH = _HEAD + r"""
$seen = @{}
foreach ($n in @(Get-Toasts)) { $seen[$n.Id] = 1 }
Write-Output '{"ready": true}'
while ($true) {
  Start-Sleep -Seconds __EVERY__
  try {
    foreach ($n in @(Get-Toasts)) {
      if (-not $seen.ContainsKey($n.Id)) { $seen[$n.Id] = 1; Write-Output (As-Json $n) }
    }
  } catch { Write-Output (@{error = $_.Exception.Message} | ConvertTo-Json -Compress) }
}
"""

NO_WINDOW = 0x08000000


def _ps_args(script):
    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]


def _parse(line):
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict) or "id" not in d:
        return d if isinstance(d, dict) else None
    texts = d.get("texts") or []
    if isinstance(texts, str):            # ConvertTo-Json writes a one-item list as a plain string
        texts = [texts]
    texts = [" ".join(str(t).split()) for t in texts if str(t).strip()]
    if (d.get("app") or "").strip():
        recent_apps.add(d["app"].strip())
    return {"id": d["id"], "app": (d.get("app") or "An app").strip(), "title": texts[0] if texts else "",
            "body": " ".join(texts[1:]), "time": d.get("time") or 0}


# ---------------------------------------------------------------- muted apps ("don't read Claude's notifications")
# The list itself lives in settings.json ("notif_muted"); these helpers only match names. Speech recognition garbles
# app names ("cloude" for Claude), so matching is forgiving, and a spoken name is snapped to an app Nova has seen.

recent_apps = set()       # app names seen in notifications since Nova started


def _norm(name):
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def same_app(a, b):
    """'Claude' ~ 'claude' ~ 'Cloude' ~ 'Claude (desktop)'; 'Mail' is not 'Gmail'."""
    import difflib
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 4 and long_.startswith(short):
        return True
    return a[0] == b[0] and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8    # first letter is rarely misheard


def is_muted(n, muted):
    app = n.get("app") if isinstance(n, dict) else n
    return any(same_app(app, m) for m in muted or ())


# ---------------------------------------------------------------- the user's iPhone, through Microsoft Phone Link
# Phone Link turns the iPhone's calls and notifications into ordinary Windows notifications from the app "Phone Link"
# (seen live 2026-11-18: app "Phone Link", title "Phone", body "<contact name or number> Missed Call"). WHO is calling
# and how to say it is the brain's job, not a rule here: it gets the notification text and answers in JSON.

PHONE_APP = "Phone Link"
PHONE_KINDS = ("call", "missed_call", "message", "other")
PHONE_PROMPT = (
    "A notification just arrived from the user's iPhone. Reply with JSON only, no other text: "
    '{"kind": "call" | "missed_call" | "message" | "other", "say": "<one short sentence Nova says out loud>"}. '
    "kind is call while the phone is ringing, missed_call after a call was missed. For a call, say who is calling if "
    "the notification shows a saved contact's name; if it shows only a phone number, or nothing, say it is an unknown "
    "number and do not read the number out. Keep it very short: at most 8 words for a call. For a message say the app and who it is from, then a short gist. "
    "Never say one-time codes, OTPs or passwords.")


# Phone Link's pop-up banners can be hidden so Nova is the only one announcing them. Windows keeps one key per source
# (one per iPhone app: "...!YourPhoneNotifications_net.whatsapp.WhatsApp", "...!YourPhoneCalling"); ShowBanner = 0 is the
# same switch as Settings → System → Notifications → Phone Link → "Show notification banners". The notifications
# still go to the notification centre, which is what the Watcher reads, so she still hears every one.
NOTIF_KEYS = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"
PHONE_AUMID = "Microsoft.YourPhone_8wekyb3d8bbwe!"


def phone_banners(show, _winreg=None):
    """Show (True) or hide (False) the pop-up banners of every Phone Link source. Returns how many keys changed."""
    try:
        winreg = _winreg or __import__("winreg")
    except ImportError:
        return 0
    changed = 0
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIF_KEYS)
    except OSError:
        return 0
    names, i = [], 0
    while True:
        try:
            names.append(winreg.EnumKey(root, i))
        except OSError:
            break
        i += 1
    winreg.CloseKey(root)
    for name in names:
        if not name.startswith(PHONE_AUMID):
            continue
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIF_KEYS + "\\" + name, 0,
                               winreg.KEY_READ | winreg.KEY_SET_VALUE)
        except OSError:
            continue
        try:
            try:
                cur = winreg.QueryValueEx(k, "ShowBanner")[0]
            except OSError:
                cur = None                                     # never set: Windows shows banners
            if show and cur == 0:
                winreg.DeleteValue(k, "ShowBanner")            # back to Windows' default
                changed += 1
            elif not show and cur != 0:
                winreg.SetValueEx(k, "ShowBanner", 0, winreg.REG_DWORD, 0)
                changed += 1
        except OSError:
            pass
        finally:
            winreg.CloseKey(k)
    return changed


def is_phone(n):
    """Is this one of the iPhone's notifications (relayed by Phone Link)?"""
    return same_app(n.get("app") if isinstance(n, dict) else n, PHONE_APP)


def phone_prompt(n):
    """The text the brain gets for one phone notification."""
    return "App: %s" % n.get("app") + chr(10) + "Title: %s" % n.get("title") + chr(10) + "Text: %s" % n.get("body")


def parse_phone_note(text, fallback):
    """The brain's JSON answer as (kind, say). Anything unreadable gives ("other", fallback)."""
    s = str(text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[4:] if s.lower().startswith("json") else s
    i, j = s.find("{"), s.rfind("}")
    try:
        d = json.loads(s[i:j + 1]) if 0 <= i < j else None
    except ValueError:
        d = None
    if not isinstance(d, dict):
        return "other", fallback
    kind = str(d.get("kind") or "").strip().lower()
    say = " ".join(str(d.get("say") or "").split())
    if not say:
        return "other", fallback
    return (kind if kind in PHONE_KINDS else "other"), say


def resolve_app(spoken, known=None):
    """The real app name the user means (from apps seen in notifications), or their words tidied up."""
    import difflib
    spoken = " ".join(str(spoken or "").split()).strip(" .'\"")
    for suffix in (" app", " application", " notifications", " notification"):
        if spoken.lower().endswith(suffix):
            spoken = spoken[: -len(suffix)].strip()
    known = sorted(set(known if known is not None else recent_apps) - {"An app"})
    for k in known:
        if _norm(k) == _norm(spoken):
            return k
    by_norm = {_norm(k): k for k in known}
    close = [c for c in difflib.get_close_matches(_norm(spoken), list(by_norm), n=3, cutoff=0.7)
             if c[:1] == _norm(spoken)[:1]]
    if close:
        return by_norm[close[0]]
    for k in known:
        if same_app(k, spoken):
            return k
    return spoken[:1].upper() + spoken[1:] if spoken else ""


def describe(n, max_len=220):
    """One notification as one spoken-friendly line: 'WhatsApp: Mom - Call me when you're free'."""
    parts = [p for p in (n.get("title"), n.get("body")) if p]
    text = " - ".join(parts) if parts else "(no text)"
    line = f"{n.get('app') or 'An app'}: {text}"
    return line if len(line) <= max_len else line[:max_len - 1] + "…"


def latest(count=5, timeout=25):
    """The newest notifications, newest first. Raises RuntimeError if Windows won't share them."""
    count = max(1, min(int(count or 5), 20))
    out = subprocess.run(_ps_args(LATEST.replace("__N__", str(count))), capture_output=True, timeout=timeout,
                         creationflags=NO_WINDOW)
    items = []
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        d = _parse(line.strip()) if line.strip() else None
        if d and d.get("error"):
            raise RuntimeError(f"Windows didn't let me read notifications ({d['error']}).")
        if d and "id" in d:
            items.append(d)
    if out.returncode and not items:
        raise RuntimeError("Couldn't read notifications: " + out.stderr.decode("utf-8", "replace").strip()[:200])
    return items


CLEAR = _HEAD + r"""
$listener.ClearNotifications()
Write-Output '{"cleared": true}'
"""


def clear_all(timeout=25):
    """Dismiss every notification waiting in Windows' notification centre. Raises RuntimeError if Windows refuses."""
    out = subprocess.run(_ps_args(CLEAR), capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        d = _parse(line.strip()) if line.strip() else None
        if d and d.get("error"):
            raise RuntimeError(f"Windows didn't let me clear notifications ({d['error']}).")
        if d and d.get("cleared"):
            return True
    raise RuntimeError(out.stderr.decode("utf-8", "replace").strip()[:200] or "no answer from Windows")


class Watcher:
    """Calls on_new(list_of_notifications) whenever new ones arrive (a burst within one poll comes as one list)."""

    def __init__(self, on_new, every=4, on_note=None):
        self.on_new, self.every, self.on_note = on_new, every, on_note or (lambda s: None)
        self.proc = None
        self.running = False
        self.lock = threading.Lock()
        self.batch, self.timer_on = [], False

    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        with self.lock:
            self.running = False
            p, self.proc = self.proc, None
        if p:
            try:
                p.kill()
            except Exception:
                pass  # intentional: the helper process may have exited

    def _run(self):
        fails = 0
        while self.running:
            try:
                self.proc = subprocess.Popen(_ps_args(WATCH.replace("__EVERY__", str(self.every))),
                                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             creationflags=NO_WINDOW)
                for raw in self.proc.stdout:
                    if not self.running:
                        break
                    d = _parse(raw.decode("utf-8", "replace").strip())
                    if not d:
                        continue
                    if d.get("error"):
                        self.on_note(f"notifications: {d['error']}")
                        if str(d["error"]).startswith("access"):
                            self.running = False
                        continue
                    if d.get("ready"):
                        self.on_note("notifications: watching")
                        fails = 0
                        continue
                    self._add(d)
            except Exception as e:
                self.on_note(f"notifications watcher failed: {e}")
            if self.running:
                fails += 1
                time.sleep(min(60, 5 * fails))

    def _add(self, n):
        """Lines from one poll arrive within milliseconds: collect them for a moment and hand them over together."""
        with self.lock:
            self.batch.append(n)
            if self.timer_on:
                return
            self.timer_on = True
        threading.Timer(0.15, self._flush).start()

    def _flush(self):
        with self.lock:
            items, self.batch, self.timer_on = self.batch, [], False
        if items:
            try:
                self.on_new(items)
            except Exception as e:
                self.on_note(f"notifications: couldn't announce ({e})")
