"""Nova as a co-worker: jobs she does in the background while you keep working.

  draft_email     - she writes the email with her brain, opens Gmail's compose page in a NEW, minimised Chrome window
                    (your current window stays in front), and fills it in. Nothing is sent - it waits for you.
  write_document  - she writes a document, saves it in Documents\\Nova, and opens it minimised in Notepad.

When a job is done she tells you ("Your email to Priya is ready - want to see it?"); "yes" (spoken, typed, or the
chat page's Show button) brings that window to the front. Jobs live in memory only.

app.py wires it up: configure(write=..., on_done=..., on_note=...).
"""
import ctypes
import itertools
import os
import re
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

from nova.actions import tools

_user32 = ctypes.windll.user32
SW_MINIMIZE, SW_SHOWMINNOACTIVE, SW_RESTORE, SW_MAXIMIZE = 6, 7, 9, 3

jobs = []                      # newest last: {id, kind, title, status, hwnd, result, error, started, finished}
_ids = itertools.count(1)
_hooks = {"write": None, "on_done": None, "on_note": None}
EMAIL_RE = re.compile(r"^[\w.+'-]+@[\w-]+(\.[\w-]+)+$")
MAX_URL_BODY = 1800            # Gmail's compose link: keep the whole URL well under browser limits
LOAD_SECS = 45


def configure(write=None, on_done=None, on_note=None):
    """write(prompt, max_tokens) -> text   (her brain)
    on_done(job)                          (tell the user)
    on_note(text)                         (her thoughts / task log)"""
    _hooks.update(write=write, on_done=on_done, on_note=on_note)


def _note(text):
    if _hooks["on_note"]:
        try:
            _hooks["on_note"](text)
        except Exception:
            pass


def running():
    return [j for j in jobs if j["status"] == "working"]


def find(which="latest"):
    """A job by id, or the newest finished one ("latest")."""
    if str(which).isdigit():
        return next((j for j in jobs if j["id"] == int(which)), None)
    done = [j for j in jobs if j["status"] == "done"]
    if which and which != "latest":
        q = str(which).lower()
        done = [j for j in done if q in j["title"].lower() or q in j["kind"]] or done
    return done[-1] if done else None


def summary():
    if not jobs:
        return "No background jobs yet."
    return "\n".join(f"#{j['id']} {j['kind'].replace('_', ' ')} '{j['title']}': {j['status']}"
                     + (f" - {j['error']}" if j.get("error") else "") for j in jobs[-8:])


def start(kind, title, work):
    """Run work(job) in the background. work fills job['result'] / job['hwnd'] or raises."""
    job = {"id": next(_ids), "kind": kind, "title": title[:80], "status": "working", "hwnd": None,
           "result": "", "error": "", "started": time.time(), "finished": None}
    jobs.append(job)
    del jobs[:-30]

    def run():
        _note(f"🧑‍💼 Background job #{job['id']} started: {kind.replace('_', ' ')} '{job['title']}'")
        try:
            work(job)
            job["status"] = "done"
        except Exception as e:
            job["status"], job["error"] = "failed", str(e)[:200]
        job["finished"] = time.time()
        _note(f"🧑‍💼 Job #{job['id']} {job['status']} after {job['finished'] - job['started']:.0f}s"
              + (f": {job['error']}" if job["error"] else ""))
        if _hooks["on_done"]:
            try:
                _hooks["on_done"](job)
            except Exception as e:
                _note(f"couldn't report job #{job['id']}: {e}")
    threading.Thread(target=run, daemon=True).start()
    return job


def show(job):
    """Bring a finished job's window to the front. Returns a sentence for the user."""
    if not job:
        return "There's no finished background job to show."
    hwnd = job.get("hwnd")
    if not hwnd or not _user32.IsWindow(hwnd):
        if job.get("path") and Path(job["path"]).exists():
            os.startfile(job["path"])          # the window was closed: open the saved file again
            return f"Opened '{job['title']}'."
        return f"The window for '{job['title']}' was closed."
    _user32.ShowWindow(hwnd, SW_RESTORE)
    _user32.ShowWindow(hwnd, SW_MAXIMIZE)
    tools._focus(hwnd)
    return f"Here it is: '{job['title']}'."


# ------------------------------------------------------------------ opening a window without stealing the screen

def _browser():
    """Chrome if it's installed (running or not), otherwise Edge."""
    for proc, rel in (("chrome.exe", r"Google\Chrome\Application\chrome.exe"),
                      ("msedge.exe", r"Microsoft\Edge\Application\msedge.exe")):
        exe = tools._exe_path(proc)
        if exe:
            return proc, exe
        for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                     os.environ.get("LOCALAPPDATA", "")):
            p = Path(base) / rel
            if base and p.exists():
                return proc, str(p)
    raise RuntimeError("I couldn't find Chrome or Edge on this laptop.")


def _windows_of(proc):
    return {h for h, _, p in tools._windows() if p.lower() == proc}


def open_hidden(args, proc, match=None, wait=15):
    """Start a program and tuck its new window away (minimised, without taking the focus), so whatever the user is
    doing stays in front. Returns the window handle (None if no new window showed up)."""
    front = _user32.GetForegroundWindow()
    before = _windows_of(proc)
    subprocess.Popen(args)
    hwnd, end = None, time.time() + wait
    while time.time() < end:
        new = _windows_of(proc) - before
        if not new and match:      # e.g. Notepad opened the file as a tab of a window that was already there
            new = {h for h, t, p in tools._windows() if p.lower() == proc and match.lower() in t.lower()}
        if new:
            hwnd = next(iter(new))
            break
        time.sleep(0.03)
    if hwnd:
        keep_hidden(hwnd, front)
        end = time.time() + 4            # a freshly started browser shows its window again once it's ready
        while time.time() < end:
            time.sleep(0.15)
            keep_hidden(hwnd, front)
    return hwnd


def keep_hidden(hwnd, front=None):
    """Minimise the job's window again if it popped up, and hand the screen back to the user's window."""
    if not _user32.IsWindow(hwnd):
        return
    if not _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_MINIMIZE if _user32.GetForegroundWindow() == hwnd else SW_SHOWMINNOACTIVE)
    if front and front != hwnd and _user32.IsWindow(front) and _user32.GetForegroundWindow() == hwnd:
        try:
            tools._focus(front)          # give the user their window back
        except Exception:
            pass


def _title(hwnd):
    n = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


# ------------------------------------------------------------------ the jobs

def gmail_compose_url(to, subject, body):
    q = {"view": "cm", "fs": "1", "su": subject, "body": body[:MAX_URL_BODY]}
    if to:
        q["to"] = to
    return "https://mail.google.com/mail/?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


EMAIL_PROMPT = """Write an email for the user. What it should say: {about}
To: {to}
{subject_line}
Write it as the user (first person), friendly and clear, ready to send. Keep it under 160 words.
Answer in exactly this form and nothing else:
Subject: <subject line>

<email body, with greeting and sign-off. Sign off with: {sign}>"""


def _parse_email(text, subject):
    text = (text or "").strip().strip("`")
    m = re.match(r"(?is)\s*subject\s*:\s*(.+?)\n(.*)", text)
    if m:
        return (subject or m.group(1).strip()), m.group(2).strip()
    return subject or "Hello", text


def draft_email(to="", about="", subject="", sign=""):
    """Start the background job. Returns (job, sentence for the brain)."""
    to, about, subject = str(to or "").strip(), str(about or "").strip(), str(subject or "").strip()
    if not about and not subject:
        raise ValueError("Tell me what the email should say.")
    address = to if EMAIL_RE.match(to) else ""
    label = subject or about[:50]

    def work(job):
        if not _hooks["write"]:
            raise RuntimeError("my brain isn't connected")
        _note("✍️ Writing the email…")
        text = _hooks["write"](EMAIL_PROMPT.format(
            about=about or subject, to=to or "(not given)", sign=sign or "the user's first name, or just 'Thanks'",
            subject_line=f"Subject must be: {subject}" if subject else "Choose a short, clear subject."), 700)
        subj, body = _parse_email(text, subject)
        if len(body) < 10:
            raise RuntimeError("my brain didn't write anything")
        job.update(subject=subj, body=body, to=to, title=subj[:80])
        proc, exe = _browser()
        _note(f"🌐 Opening Gmail in a hidden {proc.split('.')[0]} window…")
        hwnd = open_hidden([exe, "--new-window", gmail_compose_url(address, subj, body)], proc)
        if not hwnd:
            raise RuntimeError("the browser window didn't open")
        job["hwnd"] = hwnd
        end, title = time.time() + LOAD_SECS, ""
        while time.time() < end:
            if not _user32.IsWindow(hwnd):
                raise RuntimeError("the Gmail window was closed")
            keep_hidden(hwnd)
            title = _title(hwnd)
            if re.search(r"sign in|log in|accounts\.google|choose an account", title, re.I):
                job["needs_login"] = True
                break
            if re.search(r"compose|new message", title, re.I):
                break
            time.sleep(0.5)
        else:
            if not re.search(r"gmail", title, re.I):
                raise RuntimeError(f"Gmail didn't finish loading (window says '{title[:60]}')")
        if job.get("needs_login"):
            job["result"] = "Gmail wants you to sign in first - the email text is ready to paste."
            tools.set_clipboard(f"Subject: {subj}\n\n{body}")
            return
        time.sleep(4)                 # let Gmail open the compose box and save the draft
        keep_hidden(hwnd)
        job["result"] = (f"Email '{subj}' is drafted in Gmail" + (f" to {address}" if address else "")
                         + ". Not sent." + ("" if address or not to else f" I left the To box empty - I don't have {to}'s address."))

    job = start("draft_email", label, work)
    return job, (f"Started background job #{job['id']}: drafting the email in a hidden Gmail window. Tell the user "
                 "in one short sentence that you're on it and will tell them when it's ready. Don't open it yourself.")


DOC_PROMPT = """Write this document for the user: {about}
Plain text (no markdown symbols like ** or #), well organised with short paragraphs or simple numbered points.
Keep it under 400 words. Start directly with the content - no introduction about yourself."""


def _safe_name(title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip(" .")[:60]
    return name or "Note"


def write_document(title="", about=""):
    title, about = str(title or "").strip(), str(about or "").strip()
    if not about:
        raise ValueError("Tell me what the document should be about.")
    title = title or about[:40]

    def work(job):
        if not _hooks["write"]:
            raise RuntimeError("my brain isn't connected")
        _note("✍️ Writing the document…")
        text = (_hooks["write"](DOC_PROMPT.format(about=about), 900) or "").strip()
        if len(text) < 20:
            raise RuntimeError("my brain didn't write anything")
        folder = tools.HOME / "Documents" / "Nova"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{_safe_name(title)}.txt"
        n = 2
        while path.exists():
            path = folder / f"{_safe_name(title)} ({n}).txt"
            n += 1
        path.write_text(text + "\n", encoding="utf-8")
        job["path"] = str(path)
        job["hwnd"] = open_hidden(["notepad.exe", str(path)], "notepad.exe", match=path.stem)
        job["result"] = f"'{title}' is written and saved in Documents\\Nova\\{path.name}."

    job = start("write_document", title, work)
    return job, (f"Started background job #{job['id']}: writing '{title}' in the background. Tell the user in one "
                 "short sentence that you're on it and will tell them when it's ready.")
