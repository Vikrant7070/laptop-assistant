"""Nova's workers: coding agents that do whole jobs in the background (manager plan, phase 3).

Nova is the manager. She writes the brief, picks the worker, watches, checks and reports; the worker does the job:

  opencode  OpenCode + DeepSeek (cheap): ordinary fixes. `opencode run --standalone --format json -m <model>`.
            Only reads and edits files: everything else is denied. --standalone matters: without it the job runs in
            OpenCode's background service, which never sees the settings passed here (2026-11-20: it committed with a
            "deny git commit" rule in place).
  claude    Claude Code (paid, strongest): new sites/apps, hard code. `claude -p --output-format json
            --permission-mode acceptEdits --max-budget-usd N`. Shell commands it isn't allowed are refused.
            Always asked for first (app.is_risky).
  research  Nova's own research (coworker.research): reads pages in off-screen Chrome windows and writes a report
            with sources in Documents/Nova. Free, never asks. Settings: how many sources, whether the cloud brain
            may write the report. Recorded in the same job history (manager plan, phase 4 step 0).

Safety, in layers (a worker may never commit, push, publish, deploy, delete or pay - the user approves those):
  1. the worker's own permissions (OpenCode: deny by default, read/edit/search allowed; Claude Code: git
     commit/push/reset, rm, gh... denied);
  2. git refuses to push for every process the worker starts (pushInsteadOf -> a remote helper that doesn't exist),
     and the gh CLI gets a token that doesn't work;
  3. afterwards Nova compares the folder with how it was: a commit that slipped through is undone with
     `git reset --soft` (the edits stay, uncommitted) and reported.
  Jobs run only in a git folder (so every change can be seen and undone), one job per folder at a time.

Settings: data/workers.json (exe paths, models, budgets - written with defaults the first time). Job history:
data/jobs.json, each job's raw output in data/jobs/<key>.log. Runs as a coworker.py job, so "how's it going?",
"stop it", the "done - want to see it?" offer and the Telegram away report all work as for research.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from nova import store
from nova.log import log_exception

KEEP_JOBS = 200
_lock = threading.RLock()
_busy = {}                      # folder (normalised) -> coworker job id: one writer per folder
_lines = {}                     # folder (normalised) -> [tickets] in order; the first one runs, the rest wait
MAX_LINE = 3                    # jobs per folder (the running one + waiting ones)

DEFAULTS = {
    "opencode": {"exe": "", "model": "opencode/deepseek-v4.1-flash-free",
                 "fallback_models": ["opencode/big-pickle"], "timeout_min": 20},
    "claude": {"exe": "", "model": "", "budget_usd": 1.0, "timeout_min": 30},
    "research": {"max_sources": 3, "cloud_report": True},
    "daily_budget_usd": 3.0,
}
NAMES = {"opencode": "OpenCode + DeepSeek", "claude": "Claude Code", "research": "Research"}
CODERS = ("opencode", "claude")

# OpenCode: reads and edits only - everything else is denied, by default ("*") and by name. Its shell is "shell" in
# v2 ("bash" before); v2 also has "execute" (runs code that can call any of its tools - found on 2026-11-21, it
# wasn't covered by "shell": deny) and "subagent" (keeps working in the background after the run says it's done).
OPENCODE_PERMISSION = {"permission": {"*": "deny", "read": "allow", "edit": "allow", "write": "allow", "glob": "allow",
                                      "grep": "allow", "list": "allow", "todowrite": "allow", "todoread": "allow",
                                      "shell": "deny", "bash": "deny", "execute": "deny", "subagent": "deny",
                                      "task": "deny", "webfetch": "deny", "websearch": "deny",
                                      "external_directory": "deny"}}
CLAUDE_DENY = ["Bash(git commit:*)", "Bash(git push:*)", "Bash(git reset:*)", "Bash(git checkout:*)",
               "Bash(git rebase:*)", "Bash(git merge:*)", "Bash(git tag:*)", "Bash(rm:*)", "Bash(del:*)",
               "Bash(rmdir:*)", "Bash(Remove-Item:*)", "Bash(gh:*)", "Bash(npm publish:*)", "Bash(curl:*)",
               "Bash(Invoke-WebRequest:*)", "WebFetch"]
BRIEF_RULES = ("\n\nRules for this job (from Nova, the user's assistant): work only inside this folder. Do NOT commit, "
               "push, publish, deploy, delete files or pay for anything - Nova shows the user your changes and they "
               "decide. When you finish, reply with a short summary of what you changed and anything left to do.")
FREE_LIMIT = re.compile(r"free[- ]?(usage )?limit|rate.?limit|429|quota", re.I)


# ------------------------------------------------------------------ settings and finding the programs

def config():
    with _lock:
        try:
            c = json.loads(store.current().workers.read_text(encoding="utf-8"))
        except Exception:
            c = {}
            try:
                store.current().workers.parent.mkdir(parents=True, exist_ok=True)
                store.current().workers.write_text(json.dumps(DEFAULTS, indent=1), encoding="utf-8")
            except Exception as e:
                log_exception("workers: write default workers.json", e)
    out = {}
    for k, v in DEFAULTS.items():
        out[k] = {**v, **(c.get(k) or {})} if isinstance(v, dict) else c.get(k, v)
    return out


def _newest(pattern):
    hits = sorted(glob.glob(pattern), key=lambda p: os.path.getmtime(p), reverse=True)
    return hits[0] if hits else ""


def find_exe(worker, conf=None):
    """Where this worker's program is: workers.json, then the PATH, then where the desktop apps install it."""
    conf = conf or config()
    given = (conf.get(worker) or {}).get("exe") or ""
    if given and Path(given).exists():
        return given
    local = os.environ.get("LOCALAPPDATA", "")
    if worker == "opencode":
        return (shutil.which("opencode") or shutil.which("opencode-cli")
                or _newest(os.path.join(local, "Programs", "ai.opencode.desktop", "resources", "opencode-cli.exe")))
    if worker == "claude":      # the Claude desktop app keeps Claude Code in a folder per version
        return (shutil.which("claude")
                or _newest(os.path.join(local, "Claude-Data", "*", "claude-code", "*", "*", "claude.exe"))
                or _newest(os.path.join(local, "Claude-3p", "claude-code", "*", "*", "claude.exe")))
    return ""


def status():
    """One line per worker: installed or not, and the model it uses."""
    conf = config()
    lines = []
    for w in ("opencode", "claude"):
        exe = find_exe(w, conf)
        model = conf[w].get("model") or "its default model"
        state = "ready" if exe else "NOT FOUND"
        if exe and w == "claude" and claude_signed_in(conf) is False:
            state = "installed but NOT SIGNED IN"
        lines.append(f"{NAMES[w]}: {state} ({model})")
    return "\n".join(lines)


# ------------------------------------------------------------------ the settings page (⚙ Workers)

LIMITS = {"timeout_min": (1, 240), "budget_usd": (0.05, 50.0), "daily_budget_usd": (0.0, 500.0),
          "max_sources": (1, 5)}


def _num(value, key):
    lo, hi = LIMITS[key]
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key.replace('_', ' ')} must be a number")
    return round(min(hi, max(lo, v)), 2)


def _model_name(value):
    v = str(value or "").strip()
    if v and not re.fullmatch(r"[\w./:#@+-]{1,100}", v):
        raise ValueError(f"'{v[:40]}' doesn't look like a model name")
    return v


def save_settings(body):
    """⚙ Workers: change models, budgets, timeouts and program paths. Only known fields; numbers kept in range."""
    conf = config()
    oc, cc, rs = dict(conf["opencode"]), dict(conf["claude"]), dict(conf["research"])
    body = body or {}
    if "opencode_model" in body:
        oc["model"] = _model_name(body["opencode_model"]) or DEFAULTS["opencode"]["model"]
    if "opencode_fallbacks" in body:
        raw = body["opencode_fallbacks"]
        items = raw if isinstance(raw, list) else re.split(r"[,\s]+", str(raw or ""))
        oc["fallback_models"] = [_model_name(m) for m in items if str(m).strip()][:5]
    if "opencode_timeout_min" in body:
        oc["timeout_min"] = _num(body["opencode_timeout_min"], "timeout_min")
    if "claude_model" in body:
        cc["model"] = _model_name(body["claude_model"])
    if "claude_budget_usd" in body:
        cc["budget_usd"] = _num(body["claude_budget_usd"], "budget_usd")
    if "claude_timeout_min" in body:
        cc["timeout_min"] = _num(body["claude_timeout_min"], "timeout_min")
    for w, d in (("opencode", oc), ("claude", cc)):
        key = f"{w}_exe"
        if key in body:
            exe = str(body[key] or "").strip().strip('"')
            if exe and not (Path(exe).is_file() and exe.lower().endswith((".exe", ".cmd", ".bat"))):
                raise ValueError(f"{NAMES[w]}: there's no program at {exe[:120]}")
            d["exe"] = exe
    if "research_max_sources" in body:
        rs["max_sources"] = int(_num(body["research_max_sources"], "max_sources"))
    if "research_cloud_report" in body:
        rs["cloud_report"] = body["research_cloud_report"] in (True, 1, "1", "true", "on")
    daily = _num(body["daily_budget_usd"], "daily_budget_usd") if "daily_budget_usd" in body \
        else conf["daily_budget_usd"]
    new = {"opencode": oc, "claude": cc, "research": rs, "daily_budget_usd": daily}
    with _lock:
        path = store.current().workers
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(new, indent=1), encoding="utf-8")
        tmp.replace(path)
    SIGNED_IN.update(at=0.0, ok=None)            # a new program path: check the sign-in again
    return overview()


def overview(fresh=False):
    """Everything the ⚙ Workers page shows."""
    conf = config()
    exe = {w: find_exe(w, conf) for w in CODERS}
    signed = claude_signed_in(conf, fresh=fresh) if exe["claude"] else False
    recent = []
    for r in reversed(load_jobs()[-6:]):
        recent.append({"job": r.get("job"), "worker": NAMES.get(r.get("worker"), r.get("worker")),
                       "status": r.get("status"), "brief": _short(r.get("brief"), 70), "files": len(r.get("files") or []),
                       "cost": r.get("cost") or 0, "committed": bool(r.get("committed")), "started": r.get("started"),
                       "sources": len(r.get("sources") or [])})
    return {"config": conf, "exe": exe, "claude_signed_in": signed,
            "sign_in_help": "" if signed else sign_in_help(conf) if exe["claude"] else "",
            "spent_today": spent_today(), "recent": recent,
            "running": [{"folder": f, "job": j} for f, j in _busy.items()]
            + [{"folder": "", "job": j["id"]} for j in _research_running()],
            "waiting": sum(max(0, len(v) - 1) for v in _lines.values())}


# ------------------------------------------------------------------ the job history

def load_jobs():
    with _lock:
        try:
            return json.loads(store.current().jobs.read_text(encoding="utf-8"))
        except Exception:
            return []


def _save_record(rec):
    with _lock:
        recs = [r for r in load_jobs() if r.get("key") != rec["key"]] + [rec]
        path = store.current().jobs
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(recs[-KEEP_JOBS:], ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)


def spent_today(now=None):
    """Dollars Claude Code jobs have cost since midnight."""
    now = now or time.time()
    midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    return round(sum(float(r.get("cost") or 0) for r in load_jobs() if r.get("started", 0) >= midnight), 4)


# ------------------------------------------------------------------ git: the "before" and "after" of a folder

def _git(folder, *args, timeout=30):
    r = subprocess.run(["git", *args], cwd=folder, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def git_root(folder):
    code, out = _git(folder, "rev-parse", "--show-toplevel")
    return out.strip() if code == 0 else ""


def snapshot(folder):
    """HEAD, branch and the files already changed, before a worker starts."""
    _, head = _git(folder, "rev-parse", "HEAD")
    _, branch = _git(folder, "rev-parse", "--abbrev-ref", "HEAD")
    return {"head": head.strip(), "branch": branch.strip(), "dirty": sorted(_changed(folder))}


def _changed(folder):
    _, out = _git(folder, "status", "--porcelain", "--untracked-files=all")
    files = set()
    for line in out.splitlines():
        if len(line) > 3:
            name = line[3:].strip().strip('"')
            files.add(name.split(" -> ")[-1])
    return files


def after_check(folder, before):
    """After the worker: undo any commit it made (keeping the edits) and list the files it changed.
    -> {"files": [...], "undone": n commits undone or 0, "note": str}"""
    note, undone = "", 0
    _, head = _git(folder, "rev-parse", "HEAD")
    head = head.strip()
    if before["head"] and head and head != before["head"]:
        code, _ = _git(folder, "merge-base", "--is-ancestor", before["head"], head)
        _, n = _git(folder, "rev-list", "--count", f"{before['head']}..{head}")
        if code == 0:
            _git(folder, "reset", "--soft", before["head"])
            undone = int(n.strip() or 0) if n.strip().isdigit() else 1
            note = (f"The worker made {undone} commit{'s' if undone != 1 else ''} against the rules - I undid "
                    f"{'them' if undone != 1 else 'it'} (your files keep the changes, nothing is committed).")
        else:
            note = "The worker moved the branch in a way I can't simply undo - please look at the git history."
    _, branch = _git(folder, "rev-parse", "--abbrev-ref", "HEAD")
    if before["branch"] and branch.strip() and branch.strip() != before["branch"]:
        note += f" It also switched the branch to '{branch.strip()}' (it was '{before['branch']}')."
    files = sorted(_changed(folder) - set(before["dirty"]))
    return {"files": files, "undone": undone, "note": note.strip()}


# ------------------------------------------------------------------ running one worker

def _guard_env():
    """Environment for the worker and everything it starts: git can't push anywhere, gh can't sign in."""
    env = {k: v for k, v in os.environ.items() if not _inherited_claude(k, v)}
    prefixes = ["https://", "http://", "git@", "ssh://", "git://"]
    env["GIT_CONFIG_COUNT"] = str(len(prefixes))
    for i, p in enumerate(prefixes):
        env[f"GIT_CONFIG_KEY_{i}"] = "url.nova-push-blocked://.pushInsteadOf"
        env[f"GIT_CONFIG_VALUE_{i}"] = p
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_TOKEN"] = env["GITHUB_TOKEN"] = "nova-blocked"
    return env


def _inherited_claude(name, value):
    """Settings of a Claude Code session that started Nova (CLAUDE_CODE_*, a local ANTHROPIC_BASE_URL...): a worker
    must use its own sign-in, not ride on that session's private connection (2026-11-21: Nova started from a Claude
    desktop session handed these on, and the worker failed with "Not logged in")."""
    n = name.upper()
    if n.startswith("CLAUDE"):
        return True
    return n == "ANTHROPIC_BASE_URL" and bool(re.search(r"//(127\.0\.0\.1|localhost|\[::1\])", value or ""))


SIGNED_IN = {"at": 0.0, "ok": None, "exe": ""}
NOT_SIGNED_IN = re.compile(r"not logged in|please run /login|invalid api key|authentication", re.I)


def claude_signed_in(conf=None, fresh=False):
    """Is Claude Code signed in on this laptop? (`claude auth status`, remembered for 10 minutes.) None = unknown."""
    exe = find_exe("claude", conf)
    if not exe:
        return False
    if not fresh and SIGNED_IN["exe"] == exe and time.time() - SIGNED_IN["at"] < 600 and SIGNED_IN["ok"] is not None:
        return SIGNED_IN["ok"]
    ok = None
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=30, env=_guard_env(), stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = r.stdout or ""
        start = out.find("{")
        ok = bool(json.loads(out[start:]).get("loggedIn")) if start >= 0 else None
    except Exception:
        ok = None
    SIGNED_IN.update(at=time.time(), ok=ok, exe=exe)
    return ok


def sign_in_help(conf=None):
    exe = find_exe("claude", conf) or "claude"
    return f"Claude Code isn't signed in. Sign in once in PowerShell: & \"{exe}\" auth login"


def command(worker, brief, model, conf, title=""):
    """-> (args, extra env) for one run."""
    exe = find_exe(worker, conf)
    if worker == "opencode":
        args = [exe, "run", "--standalone", "--format", "json", "--archived", "--title", title or "Nova job"]
        if model:
            args += ["-m", model]
        return args + [brief], {"OPENCODE_CONFIG_CONTENT": json.dumps(OPENCODE_PERMISSION)}
    args = [exe, "-p", brief, "--output-format", "json", "--permission-mode", "acceptEdits",
            "--max-budget-usd", f"{float(conf['claude'].get('budget_usd') or 1.0):.2f}",
            "--disallowedTools", *CLAUDE_DENY]
    if model:
        args += ["--model", model]
    return args, {}


def _spawn(args, cwd, env):
    return subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _kill(proc):
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass  # intentional: the process tree may already be gone; proc.kill() below is the fallback
    try:
        proc.kill()
    except Exception:
        pass  # intentional: already exited - killing a finished process is the expected race


def _step_from(event):
    """An OpenCode JSON line -> what it's doing now ('editing README.md'), or ''."""
    if event.get("type") != "tool_use":
        return ""
    part = event.get("part") or {}
    tool = part.get("tool", "")
    inp = (part.get("state") or {}).get("input") or {}
    target = inp.get("path") or inp.get("filePath") or inp.get("pattern") or ""
    verb = {"read": "reading", "edit": "editing", "write": "writing", "glob": "looking for", "grep": "searching",
            "list": "looking in"}.get(tool, f"using {tool}")
    return f"{verb} {os.path.basename(str(target)) or target}".strip()


def parse_opencode(lines):
    """OpenCode --format json output -> {"text", "cost", "error", "limit"}."""
    texts, cost, error, limit = [], 0.0, "", False
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        part = e.get("part") or {}
        if e.get("type") == "text" and part.get("text"):
            texts.append(part["text"])
        elif e.get("type") == "step_finish":
            cost += float(part.get("cost") or 0)
        elif e.get("type") == "error":
            err = e.get("error") or {}
            error = str(err.get("message") or err)[:300]
            limit = limit or bool(FREE_LIMIT.search(f"{err.get('type', '')} {error} {err.get('status', '')}"))
    return {"text": texts[-1].strip() if texts else "", "cost": round(cost, 4), "error": error, "limit": limit}


def parse_claude(lines):
    """Claude Code --output-format json -> {"text", "cost", "error", "denied"}."""
    raw = "\n".join(lines).strip()
    start = raw.find("{")
    try:
        r = json.loads(raw[start:]) if start >= 0 else {}
    except Exception:
        r = {}
    if not r:
        return {"text": "", "cost": 0.0, "error": (raw[-300:] or "no answer from Claude Code"), "denied": []}
    denied = [str((d or {}).get("tool_name") or d)[:60] for d in r.get("permission_denials") or []]
    err = "" if not r.get("is_error") else str(r.get("result") or r.get("subtype") or "error")[:300]
    return {"text": str(r.get("result") or "").strip(), "cost": float(r.get("total_cost_usd") or 0), "error": err,
            "denied": denied}


def run_once(worker, brief, folder, model, conf, job=None, checkpoint=None, log=None):
    """Run the worker to the end (or until stopped / timed out). -> parsed result + "lines"."""
    args, extra = command(worker, brief, model, conf, title=f"Nova job #{job['id']}" if job else "Nova job")
    # PWD too: OpenCode takes its project folder from an inherited PWD over the real working folder (2026-11-21:
    # started from Git Bash, it worked on Nova's own README instead of the job's).
    env = {**_guard_env(), **extra, "PWD": os.path.normpath(str(folder))}
    proc = _spawn(args, folder, env)
    lines, end = [], time.time() + 60 * float(conf[worker].get("timeout_min") or 20)

    def read():
        for line in proc.stdout:
            lines.append(line)
            if log:
                log.write(line)
            if job is not None and worker == "opencode" and line.lstrip().startswith("{"):
                try:
                    step = _step_from(json.loads(line))
                except Exception:
                    step = ""
                if step:
                    job["step"] = step
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        while proc.poll() is None:
            if checkpoint:
                checkpoint()
            if time.time() > end:
                _kill(proc)
                raise RuntimeError(f"{NAMES[worker]} took longer than {conf[worker].get('timeout_min')} minutes, "
                                   "so I stopped it")
            time.sleep(0.5)
    except BaseException:
        if proc.poll() is None:
            _kill(proc)
        raise
    reader.join(5)
    out = parse_opencode(lines) if worker == "opencode" else parse_claude(lines)
    out["lines"] = lines
    out["exit"] = proc.returncode
    return out


# ------------------------------------------------------------------ a job: brief -> worker -> check -> report

def pick_worker(worker="auto", size="small", kind="coding"):
    """Which worker does this job: 'research' for finding things out on the web, else a coding worker - the cheap
    one for small jobs, Claude Code for big ones or when asked for by name."""
    w = str(worker or "auto").lower()
    if w in ("research", "web", "researcher") or str(kind or "").lower() == "research":
        return "research"
    if w in ("claude", "claude code", "claude-code"):
        return "claude"
    if w in ("opencode", "deepseek", "open code"):
        return "opencode"
    return "claude" if str(size).lower() == "big" else "opencode"


def _norm(folder):
    return os.path.normcase(os.path.abspath(folder))


def check_start(brief, folder, worker):
    """Why this job can't start, or "" (and the git root to run it in)."""
    if not str(brief or "").strip():
        return "Tell the worker what to do (the brief is empty).", ""
    folder = os.path.expandvars(os.path.expanduser(str(folder or "").strip().strip('"')))
    if not folder or not os.path.isdir(folder):
        return ("Not started: I need the project folder - ask the user which folder it is (the full path), "
                "then call start_coding_job again with it."), ""
    root = git_root(folder)
    if not root:
        return ("Not started: that folder isn't a git project, so I couldn't show the user what the worker changed "
                "or undo it. Ask the user if they want to run 'git init' there first."), ""
    conf = config()
    if not find_exe(worker, conf):
        return (f"Not started: I can't find {NAMES[worker]} on this laptop. Tell the user (set its path in "
                "data/workers.json)."), ""
    if len(_lines.get(_norm(root), [])) >= MAX_LINE:
        return (f"Not started: {MAX_LINE} jobs are already lined up for that folder. Wait for them, or stop one "
                "first."), ""
    if worker == "claude" and claude_signed_in(conf) is False:
        return "Not started: " + sign_in_help(conf) + " - tell the user that.", ""
    if worker == "claude" and spent_today() >= float(conf.get("daily_budget_usd") or 0):
        return (f"Not started: Claude Code jobs have already cost ${spent_today():.2f} today, which is the daily "
                "limit in data/workers.json. Tell the user."), ""
    return "", root


def start(brief, folder, worker="auto", size="small", start_job=None):
    """Start a coding job in the background. start_job = coworker.start (passed in so tests can run it inline).
    Returns a sentence for the brain."""
    from nova.actions import coworker
    w = pick_worker(worker, size)
    why, root = check_start(brief, folder, w)
    if why:
        return why
    conf = config()
    norm = _norm(root)
    ticket = {"job": None}                 # one writer per folder: jobs for a busy folder wait in line, in order
    with _lock:
        if len(_lines.get(norm, [])) >= MAX_LINE:
            return f"Not started: {MAX_LINE} jobs are already lined up for that folder."
        ahead = [t for t in _lines.get(norm, [])]
        _lines.setdefault(norm, []).append(ticket)
    title = re.sub(r"\s+", " ", str(brief)).strip()[:70]

    def work(job):
        ticket["job"] = job["id"]
        job.update(worker=w, path=root)
        key = f"{int(job['started'])}-{job['id']}"
        rec = {"key": key, "job": job["id"], "worker": w, "model": "", "brief": str(brief)[:2000], "folder": root,
               "status": "queued" if _lines[norm][0] is not ticket else "running", "started": job["started"],
               "finished": None, "cost": 0.0, "files": [], "result": "", "note": "", "log": str(store.current().job_logs / f"{key}.log")}
        _save_record(rec)
        before = None
        try:
            while True:                    # wait for our turn (stopping it here just takes it out of the line)
                with _lock:
                    first = _lines[norm][0]
                if first is ticket:
                    break
                job["step"] = f"waiting in line behind job #{first['job'] or '?'}"
                coworker._sleep(0.5)
            _busy[norm] = job["id"]
            if rec["status"] == "queued":
                rec.update(status="running", started=time.time())
                _save_record(rec)
            before = snapshot(root)
            store.current().job_logs.mkdir(parents=True, exist_ok=True)
            models = ([conf[w].get("model")] + list(conf[w].get("fallback_models") or [])) if w == "opencode" \
                else [conf[w].get("model") or ""]
            out, used = None, ""
            with open(rec["log"], "a", encoding="utf-8") as log:
                for m in models:
                    job["step"] = f"{NAMES[w]} is starting" + (f" ({m})" if m else "")
                    out, used = run_once(w, str(brief) + BRIEF_RULES, root, m, conf, job, coworker.checkpoint, log), m
                    if not out.get("limit"):
                        break
                    coworker._note(f"🧑‍💻 {m} is at its free limit - trying the next model.")
            job["step"] = "checking what it changed"
            rec["status"] = "checking"
            _save_record(rec)
            check = after_check(root, before)
            rec.update(model=used, cost=out["cost"], files=check["files"], note=check["note"],
                       result=out["text"][:2000])
            job.update(path=root, files=check["files"], worker=w, cost=out["cost"], model=used)
            if w == "claude" and out["error"] and NOT_SIGNED_IN.search(out["error"]) and not check["files"]:
                SIGNED_IN.update(at=time.time(), ok=False)
                rec["status"] = "failed"
                raise RuntimeError(sign_in_help(conf))          # whole: the command at its end must survive
            if out["error"] and not check["files"]:
                rec["status"] = "failed"
                raise RuntimeError(f"{NAMES[w]} stopped with an error: {out['error'][:160]}" + (
                    " If you want, I can give it to Claude Code instead (that one costs money)." if w == "opencode"
                    else ""))
            if not check["files"]:
                rec["status"] = "done"
                job["result"] = (f"{NAMES[w]} finished, but it didn't change any files. It said: "
                                 f"{_short(out['text'])}" + (f" {check['note']}" if check["note"] else ""))
                return
            rec["status"] = "needs-user" if out.get("denied") else "done"
            n = len(check["files"])
            names = ", ".join(check["files"][:4]) + (f" and {n - 4} more" if n > 4 else "")
            job["result"] = (f"{NAMES[w]} finished the job '{title}' and changed {n} file{'s' if n != 1 else ''}: "
                             f"{names}. Nothing is committed - say 'commit it' when you've had a look."
                             + (f" {check['note']}" if check["note"] else "")
                             + (f" It wasn't allowed to: {', '.join(out['denied'][:3])}." if out.get("denied") else ""))
        except coworker.Stopped:
            rec["status"] = "stopped"
            if before:
                try:
                    check = after_check(root, before)
                    rec.update(files=check["files"], note=check["note"])
                except Exception as e:
                    log_exception("workers: after-check of a stopped job", e)
            raise
        except Exception as e:
            rec["status"] = "failed"
            rec["note"] = str(e)[:300]
            raise
        finally:
            rec["finished"] = time.time()
            _save_record(rec)
            _leave_line(norm, ticket, job["id"])

    try:
        job = (start_job or coworker.start)("coding", title, work)
    except Exception:
        _leave_line(norm, ticket, None)
        raise
    ticket["job"] = job["id"]
    extra = "" if w == "opencode" else f" (Claude Code, up to ${float(conf['claude'].get('budget_usd') or 1):.2f})"
    if ahead:
        return (f"Queued background job #{job['id']}: {NAMES[w]}{extra} will work on '{title}' in {root} as soon as "
                f"job #{ahead[-1]['job'] or '?'} in that folder is done (one worker per folder at a time). Tell the "
                "user in one short sentence. Don't do the job yourself.")
    return (f"Started background job #{job['id']}: {NAMES[w]}{extra} is working on '{title}' in {root}. Tell the user "
            "in one short sentence that it's running in the background and you'll tell them when it's done. "
            "Don't do the job yourself.")


def _leave_line(norm, ticket, job_id):
    with _lock:
        line = _lines.get(norm, [])
        if ticket in line:
            line.remove(ticket)
        if not line:
            _lines.pop(norm, None)
        if job_id is not None and _busy.get(norm) == job_id:
            _busy.pop(norm, None)


def _short(text, n=220):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _day_matches(words, started):
    """'the research from Tuesday' / 'yesterday' / 'today' against when a job started. None = no day was named."""
    if not started:
        return None
    day = time.localtime(started)
    today = time.localtime()
    for w in words:
        if w in DAYS:
            return day.tm_wday == DAYS.index(w) and time.time() - started < 7 * 86400
        if w == "today":
            return day[:3] == today[:3]
        if w == "yesterday":
            return day[:3] == time.localtime(time.time() - 86400)[:3]
    return None


def last_record(which="latest", need_files=True, kinds=CODERS):
    """The newest job record that fits: 'latest', a job number ('#3'), or words from its brief / a day ('the research
    from Tuesday'). kinds = which workers' records (None = all)."""
    recs = [r for r in load_jobs() if (r.get("files") or not need_files) and (kinds is None or r.get("worker") in kinds)]
    w = str(which or "latest").strip().lower()
    if w.lstrip("#").isdigit():
        n = int(w.lstrip("#"))
        recs = [r for r in recs if r.get("job") == n] or []
    elif w not in ("latest", "last", ""):
        words = [x for x in re.findall(r"[a-z0-9]+", w) if len(x) > 2]
        dated = [r for r in recs if _day_matches(words, r.get("started")) is not False]   # a named day must fit
        named = [x for x in words if x not in DAYS + LOOKUP_FILLER]
        hit = [r for r in dated if any(x in str(r.get("brief", "")).lower() for x in named)]
        recs = hit or dated                    # words that match nothing: the newest one (of that day, if named)
    return recs[-1] if recs else None


LOOKUP_FILLER = ("today", "yesterday", "research", "job", "the", "from", "last", "latest", "one", "that", "this",
                 "about", "coding", "did", "you", "with", "on", "for")


def changes(which="latest", max_chars=1500):
    """What a finished job changed, short enough for a text message: the files, then the diff."""
    rec = last_record(which)
    if not rec:
        return "There's no finished coding job with changes."
    root = rec["folder"]
    files = [f for f in rec["files"] if f in _changed(root)]
    if not files:
        return "Those changes aren't there any more (already committed or undone)."
    _, untracked = _git(root, "ls-files", "--others", "--exclude-standard", "--", *files)
    new = set(untracked.split())
    _, diff = _git(root, "diff", "--no-color", "-U1", "--", *[f for f in files if f not in new])
    body = [ln for ln in diff.splitlines() if not ln.startswith(("diff --git", "index ", "--- ", "+++ ", "@@"))]
    text = (f"Job #{rec['job']} ({NAMES.get(rec['worker'], rec['worker'])}) changed {len(files)} file"
            f"{'s' if len(files) != 1 else ''} in {root}: {', '.join(files)}"
            + (f" (new: {', '.join(sorted(new))})" if new else "") + ". Not committed.\n" + "\n".join(body))
    return text if len(text) <= max_chars else text[:max_chars].rsplit("\n", 1)[0] + "\n…"


def retry_target(which="latest", worker=""):
    """What 'try again' means -> (record, worker). No worker named: the newest job of any kind - research runs again
    as research, a coding job goes to Claude Code (the usual reason to retry is that the cheap one failed)."""
    w = str(worker or "").strip().lower()
    if w in ("", "auto"):
        rec = last_record(which, need_files=False, kinds=None)
        return rec, ("research" if rec and rec.get("worker") == "research" else "claude")
    w = pick_worker(w)
    return last_record(which, need_files=False, kinds=("research",) if w == "research" else CODERS), w


def retry(which="latest", worker=""):
    """Run the last job (usually one that failed) again: research again, or a coding job with another worker (same
    brief and folder)."""
    rec, w = retry_target(which, worker)
    if not rec:
        return "There's no job to try again."
    if rec.get("status") in ("running", "queued", "checking"):
        return f"Job #{rec['job']} is still going - wait for it, or stop it first."
    if w == "research":
        return research(rec.get("topic") or rec["brief"], rec.get("focus", ""), rec.get("kind", "general"))
    return start(rec["brief"], rec["folder"], worker=w)


# ------------------------------------------------------------------ the research worker (coworker.research)

def _research_running():
    from nova.actions import coworker
    return [j for j in coworker.running() if j["kind"] == "research"]


def research(topic, focus="", kind="general", max_sources=None, start_job=None):
    """Start a research job (coworker.research does the reading and writing), recorded in the job history like a
    coding job. max_sources: what the user asked for, else ⚙ Workers' number. Returns a sentence for the brain."""
    from nova.actions import coworker
    conf = config()["research"]
    try:
        n = int(max_sources or conf.get("max_sources") or 3)
    except (TypeError, ValueError):
        n = int(conf.get("max_sources") or 3)
    n = max(1, min(n, 5))
    local = not conf.get("cloud_report", True)
    topic = re.sub(r"\s+", " ", str(topic or "")).strip()
    focus = str(focus or "").strip()

    def starter(job_kind, title, work):
        def recorded(job):
            job["worker"] = "research"
            key = f"{int(job['started'])}-{job['id']}"
            rec = {"key": key, "job": job["id"], "worker": "research", "model": "local brain" if local else "",
                   "brief": topic + (f" - {focus}" if focus else ""), "topic": topic, "focus": focus, "kind": kind,
                   "folder": "", "status": "running", "started": job["started"], "finished": None, "cost": 0.0,
                   "files": [], "result": "", "note": "", "log": "", "path": "", "sources": [],
                   "max_sources": n}
            _save_record(rec)
            try:
                work(job)
                rec.update(status="done", result=str(job.get("result") or "")[:2000], path=job.get("path") or "",
                           sources=list(job.get("sources") or []))
            except coworker.Stopped:
                rec["status"] = "stopped"
                raise
            except Exception as e:
                rec.update(status="failed", note=str(e)[:300])
                raise
            finally:
                rec["finished"] = time.time()
                _save_record(rec)
        return (start_job or coworker.start)(job_kind, title, recorded)

    return coworker.research(topic=topic, focus=focus, kind=kind, max_sources=n, local=local, start_job=starter)[1]


def report(which="latest", max_chars=1500):
    """A finished research job's report, short enough for a text message."""
    rec = last_record(which, need_files=False, kinds=("research",))
    if not rec:
        return "There's no research job yet."
    if rec.get("status") != "done":
        return f"Research #{rec['job']} on '{rec.get('topic') or rec['brief']}' {rec.get('status')}" + (
            f": {rec['note']}" if rec.get("note") else ".")
    text = ""
    try:
        text = Path(rec["path"]).read_text(encoding="utf-8") if rec.get("path") else ""
    except OSError:
        text = ""  # intentional: the file was moved or deleted - the headline and sources from the record still help
    text = text.strip() or f"Research: {rec.get('topic')}\n\n{rec.get('result')}\n\nSources:\n" + "\n".join(
        rec.get("sources") or [])
    return text if len(text) <= max_chars else text[:max_chars].rsplit("\n", 1)[0] + "\n…"


def commit(which="latest", message=""):
    """Commit the files a finished job changed (only those), after the user said yes. Never pushes."""
    rec = last_record(which)
    if not rec:
        return "There's no finished coding job with changes to commit."
    if rec.get("status") == "running":
        return "That job is still running - wait for it to finish first."
    root = rec["folder"]
    files = [f for f in rec["files"] if f in _changed(root)]
    if not files:
        return "Those changes aren't there any more (already committed or undone), so there's nothing to commit."
    msg = re.sub(r"\s+", " ", str(message or "").strip())[:120] or f"{rec['brief'][:60].strip()} (by {NAMES[rec['worker']]})"
    code, out = _git(root, "add", "--", *files)
    if code:
        return f"Couldn't stage the files: {out.strip()[:200]}"
    code, out = _git(root, "commit", "-m", msg, "--", *files)
    if code:
        return f"Couldn't commit: {out.strip()[:200]}"
    rec["committed"] = True
    _save_record(rec)
    return f"Committed {len(files)} file{'s' if len(files) != 1 else ''} in {root}: \"{msg}\". Nothing was pushed."
