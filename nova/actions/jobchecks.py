"""Checks on a finished worker job (manager plan 4c-2): the worker's "done" is never the verdict.

    coding(root, folder, files, env, checkpoint) -> [check]   files changed only inside the folder; the project's
                                                              tests, by the command found from its files
    research(job, wanted)                         -> [check]   at least 2 sources cited; the report file exists
    verdict(checks)  -> "passed" | "failed" | "not checked" (every check skipped) | "" (no checks)
    left(checks, said) -> the "couldn't finish" list: failed checks + what the worker said it left
    summary(checks)  -> one short sentence for the report

A check is {"name", "state": "passed" | "failed" | "skipped", "why"} (+ "detail": the failing output, for 4c-3's
send-back). A check that could not run is *skipped, with why* ("tests: skipped - no test command found"), never left
out and never counted as passed (lingtai). The test command comes from the project's files (pitboss):

    pyproject.toml / setup.py / setup.cfg / pytest.ini / tox.ini  -> pytest (the project's .venv first)
    package.json with a real "test" script                        -> npm / pnpm / yarn test, by lock file
    Cargo.toml -> cargo test          go.mod -> go test ./...

Tests run with the worker's safety environment (git can't push, gh can't sign in), with a time limit, and stop when
the job is stopped. Nothing is installed to make them run: missing tools or dependencies -> skipped, with why.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from nova.log import log_exception

TEST_SECS = 300              # a test run longer than this is stopped and fails the check
OUT_KEEP = 1500              # characters of failing test output kept for the send-back
MIN_SOURCES = 2
PY_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini")
NO_NPM_TEST = re.compile(r"no test specified", re.I)
LEFT_LINE = re.compile(r"^\W*(?:left to do|not done|couldn'?t finish|still to do)\s*:\s*(.+)$", re.I | re.M)
NOTHING = re.compile(r"^(?:nothing|none|n/?a|-|everything (?:is )?done)\.?$", re.I)


def _check(name, state, why, detail=""):
    c = {"name": name, "state": state, "why": " ".join(str(why).split())[:200]}
    if detail:
        c["detail"] = str(detail)[-OUT_KEEP:]
    return c


# ------------------------------------------------------------------ coding: files inside the folder

def _rel(root, folder):
    """The job's folder relative to the git root: "." for the whole project, or when the two can't be compared
    (another drive spelling, short names - never call a file "outside" on a guess)."""
    try:
        rel = os.path.relpath(os.path.abspath(folder), os.path.abspath(root))
    except ValueError:
        return "."
    return "." if rel in (".", "") or rel.startswith("..") else rel.replace("\\", "/").rstrip("/")


def outside(root, folder, files):
    """The changed files (relative to the git root) that are not inside the job's folder."""
    rel = _rel(root, folder)
    if rel == ".":
        return []
    return [f for f in files if not str(f).replace("\\", "/").startswith(rel + "/")]


def files_inside(root, folder, files):
    """The files the worker changed (relative to the git root) must all be inside the folder the job was given."""
    if not files:
        return _check("files", "skipped", "no files changed")
    rel, out = _rel(root, folder), outside(root, folder, files)
    if out:
        return _check("files", "failed", f"changed files outside {rel}: " + ", ".join(out[:5])
                      + (f" and {len(out) - 5} more" if len(out) > 5 else ""))
    return _check("files", "passed", f"{len(files)} changed, all inside " + ("the project folder" if rel == "." else rel))


def undo(root, files, keep):
    """The brief guard: put back the files a worker changed outside its folder. A file git knows is restored from
    HEAD; a new file is moved (never deleted) to `keep`. -> ["path (put back)" / "path (moved to …)", ...]"""
    from nova.actions import workers
    done = []
    for f in files:
        try:
            if workers._git(root, "cat-file", "-e", f"HEAD:{f}")[0] == 0:
                code, msg = workers._git(root, "restore", "--source=HEAD", "--staged", "--worktree", "--", f)
                done.append(f"{f} (put back)" if code == 0 else f"{f} (couldn't put it back: {msg.strip()[:80]})")
                continue
            workers._git(root, "restore", "--staged", "--", f)          # a new file a slipped commit staged
            src = Path(root, f)
            if src.exists():
                dst = Path(keep, f)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                done.append(f"{f} (moved to {dst})")
        except Exception as e:
            log_exception("jobchecks: undoing a change outside the folder", e)
            done.append(f"{f} (couldn't undo it: {e.__class__.__name__})")
    return done


# ------------------------------------------------------------------ coding: the test command

def _venv_python(folder):
    for d in (".venv", "venv", "env"):
        for p in (Path(folder, d, "Scripts", "python.exe"), Path(folder, d, "bin", "python")):
            if p.is_file():
                return str(p)
    return ""


def _which(name):
    return shutil.which(name)


def _npm_command(folder):
    try:
        pkg = json.loads(Path(folder, "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"package.json can't be read ({e.__class__.__name__})"
    script = ((pkg.get("scripts") or {}) if isinstance(pkg, dict) else {}).get("test")
    if not script or NO_NPM_TEST.search(str(script)):
        return None, "package.json has no test script"
    tool = ("pnpm" if Path(folder, "pnpm-lock.yaml").exists() else
            "yarn" if Path(folder, "yarn.lock").exists() else "npm")
    exe = _which(tool)
    if not exe:
        return None, f"{tool} isn't installed"
    if not Path(folder, "node_modules").is_dir():
        return None, "its packages aren't installed (no node_modules) - I don't install anything on my own"
    return [exe, "test"], ""


def test_command(folder):
    """-> (argv, "") or (None, why the tests can't run). Looks only at the project's own files."""
    f = Path(folder)
    if any((f / m).exists() for m in PY_MARKERS):
        py = _venv_python(folder)
        if py:
            return [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"], ""
        exe = _which("pytest")
        if exe:
            return [exe, "-q", "-p", "no:cacheprovider"], ""
        return None, "pytest isn't installed (no .venv in the project, no pytest on PATH)"
    if (f / "package.json").exists():
        return _npm_command(folder)
    if (f / "Cargo.toml").exists():
        exe = _which("cargo")
        return ([exe, "test"], "") if exe else (None, "cargo isn't installed")
    if (f / "go.mod").exists():
        exe = _which("go")
        return ([exe, "test", "./..."], "") if exe else (None, "go isn't installed")
    return None, "no test command found"


def _project_dir(root, folder):
    """Where the tests live: the job's folder, else the nearest folder above it (up to the git root) that has them."""
    here, top = Path(os.path.abspath(folder)), Path(os.path.abspath(root))
    while True:
        argv, why = test_command(here)
        if argv or why != "no test command found" or here == top or top not in here.parents:
            return here
        here = here.parent


def _kill(proc):
    from nova.actions import workers
    workers._kill(proc)


def _failing(text):
    """The short reason from test output: pytest's FAILED lines and its last summary line, else the last lines."""
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    names = [m.group(1).split("::")[-1] for ln in lines for m in [re.match(r"FAILED\s+(\S+)", ln)] if m]
    tail = next((ln.strip("= ") for ln in reversed(lines) if re.search(r"\b(failed|error|passed)\b", ln, re.I)),
                lines[-1] if lines else "no output")
    return tail[:120] + (f" ({', '.join(names[:4])}{' …' if len(names) > 4 else ''})" if names else "")


def run_tests(cwd, argv, env=None, checkpoint=None, secs=None, name="tests"):
    """Run the test command (or a Phase 5 judge's: lint, build, lighthouse - `name`); the job's Stop stops it too.
    -> a check called `name`."""
    secs = secs or TEST_SECS
    shown = " ".join([os.path.basename(argv[0])] + argv[1:]).replace("python.exe -m ", "").replace("python -m ", "")
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), env={**(env or os.environ), "CI": "1", "PYTHONDONTWRITEBYTECODE": "1"}, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        return _check(name, "skipped", f"{shown} couldn't start ({e.strerror or e})")
    out = []
    from nova import runtime
    reader = runtime.spawn("jobchecks.read", lambda: out.extend(proc.stdout), start=False)
    reader.start()
    end = time.time() + secs
    try:
        while proc.poll() is None:
            if checkpoint:
                checkpoint()
            if time.time() > end:
                _kill(proc)
                reader.join(5)
                return _check(name, "failed", f"{shown} ran longer than {secs // 60 or 1} min, so I stopped it",
                              "".join(out))
            time.sleep(0.2)
    except BaseException:
        if proc.poll() is None:
            _kill(proc)
        raise
    reader.join(5)
    text = "".join(out)
    if proc.returncode == 0:
        return _check(name, "passed", f"{shown}: {_failing(text)}")
    if proc.returncode == 5 and "pytest" in shown and name == "tests":            # pytest: no tests were collected
        return _check(name, "skipped", "the project has no tests yet")
    return _check(name, "failed", f"{shown}: {_failing(text)}", text)


def coding(root, folder, files, env=None, checkpoint=None, kind=""):
    """The checks of a finished coding job: files, tests, then Phase 5's judges (lint/build, and the website judge
    for a new site / app or a folder with a page). Never raises (a check that crashes is skipped, with why).
    kind: the manager's job type ("new site", "feature", ...), "" when unknown."""
    checks = [files_inside(root, folder or root, files)]
    if not files:
        return checks + [_check("tests", "skipped", "no files changed")]
    try:
        cwd = _project_dir(root, folder or root)
        argv, why = test_command(cwd)
        checks.append(run_tests(cwd, argv, env, checkpoint) if argv else _check("tests", "skipped", why))
    except Exception as e:
        if e.__class__.__name__ == "Stopped":
            raise
        log_exception("jobchecks: running the tests", e)
        checks.append(_check("tests", "skipped", f"the test run crashed ({e.__class__.__name__})"))
    return checks + judged(root, folder or root, env, checkpoint, kind)


def judged(root, folder, env=None, checkpoint=None, kind=""):
    """Phase 5's judges on a coding job's folder: lint / build, then the website judge when it's a site."""
    from nova.actions import judges, sitejudge
    out, built = [], ""
    try:
        conf = judges.limits()
        cwd = judges.project(root, folder, judges._sets_up_code)
        out, built = judges.code(cwd, env, checkpoint, conf)
        site = judges.project(root, folder, sitejudge.looks_like_site)
        if sitejudge.wanted(site, kind):
            out += sitejudge.site(site, kind, env, checkpoint, conf, built if Path(site) == Path(cwd) else "")
    except Exception as e:
        if e.__class__.__name__ == "Stopped":
            raise
        log_exception("jobchecks: running the judges", e)
        out.append(_check("judges", "skipped", f"the judges crashed ({e.__class__.__name__})"))
    return out


# ------------------------------------------------------------------ research

def research(job, wanted=3):
    """A finished research job: enough sources cited (2, or fewer when the user asked for fewer) and the report file
    is really there."""
    sources = list(job.get("sources") or [])
    cited = job.get("cited")
    n = len(cited) if isinstance(cited, list) else len(sources)
    need = max(1, min(MIN_SOURCES, int(wanted or MIN_SOURCES)))
    word = "cited" if isinstance(cited, list) else "listed"
    checks = [_check("sources", "passed" if n >= need else "failed",
                     f"{n} source{'s' if n != 1 else ''} {word}" + ("" if n >= need else f" (need {need})"))]
    path = str(job.get("path") or "")
    if not path:
        checks.append(_check("report", "failed", "no report file was saved"))
    else:
        try:
            size = os.path.getsize(path)
            checks.append(_check("report", "passed" if size > 0 else "failed",
                                 f"{os.path.basename(path)} is there" if size > 0 else f"{os.path.basename(path)} is empty"))
        except OSError:
            checks.append(_check("report", "failed", f"the report file is missing ({path})"))
    if checks[-1]["state"] == "passed":                      # Phase 5: the research judge reads the report
        from nova.actions import judges
        try:
            checks += judges.research(job)
        except Exception as e:
            log_exception("jobchecks: judging the research report", e)
            checks.append(_check("judges", "skipped", f"the research judge crashed ({e.__class__.__name__})"))
    return checks


# ------------------------------------------------------------------ 4c-3: send back once, within limits

SEND_BACKS = 1               # a failed check goes back to the same worker this many times (manager setting)
JOB_MIN = 60                 # minutes for the whole job, every round together (manager setting)
DETAIL_KEEP = 1200           # characters of the failing output in the send-back brief
NL = chr(10)


def send_back_brief(brief, checks):
    """The same brief, plus the exact failing check and the end of its output."""
    failed = [c for c in checks or [] if c["state"] == "failed"]
    lines = [f"- {c['name']}: {c['why']}" for c in failed]
    detail = NL.join(c.get("detail", "")[-DETAIL_KEEP:] for c in failed if c.get("detail")).strip()
    parts = [str(brief).strip(), "Nova checked your work and this check failed:" + NL + NL.join(lines)]
    if detail:
        parts.append("The end of its output:" + NL + detail)
    parts.append("Fix what makes this check fail, inside this folder. Don't change the tests to make them pass.")
    return (NL + NL).join(parts)


def limit_hit(manager, worker, started, sent_back, conf=None, spent=0.0, now=None):
    """Why the job may NOT go back to the worker again, or "": rounds, time for the whole job, money (paid workers:
    one more round at its budget would pass the daily limit)."""
    manager, conf = manager or {}, conf or {}
    rounds = int(manager.get("send_backs", SEND_BACKS))
    if sent_back >= rounds:
        return (f"rounds: it was already sent back {sent_back} time{'s' if sent_back != 1 else ''}" if rounds
                else "rounds: sending back is turned off")
    mins = float(manager.get("job_min") or JOB_MIN)
    if (now or time.time()) - float(started or 0) > mins * 60:
        return f"time: the job has used its {mins:g} minutes"
    if worker == "claude":
        budget = float((conf.get("claude") or {}).get("budget_usd") or 1.0)
        daily = float(conf.get("daily_budget_usd") or 0)
        if spent + budget > daily:
            return (f"money: Claude Code jobs have cost ${spent:.2f} today; one more round (up to ${budget:.2f}) "
                    f"would pass the daily limit of ${daily:.2f}")
    return ""


# ------------------------------------------------------------------ the verdict and the words

def verdict(checks):
    states = [c["state"] for c in checks or []]
    if not states:
        return ""
    if "failed" in states:
        return "failed"
    return "not checked" if all(s == "skipped" for s in states) else "passed"


def worker_left(text):
    """What the worker said it left: its "Left to do: …" line(s), "nothing" dropped."""
    out = []
    for m in LEFT_LINE.finditer(str(text or "")):
        item = " ".join(m.group(1).split())[:200]
        if item and not NOTHING.match(item):
            out.append(item)
    return out[:5]


def left(checks, said=""):
    """The "couldn't finish" list: each failed check, then what the worker itself said it didn't do."""
    return [f"{c['name']}: {c['why']}" for c in checks or [] if c["state"] == "failed"] + worker_left(said)


def describe(c):
    return f"{c['name']} {c['state']}" if c["state"] == "passed" else f"{c['name']}: {c['state']} - {c['why']}"


def summary(checks):
    """One sentence: what failed, else 'done, not checked', else what passed (with any skipped one and why)."""
    v = verdict(checks)
    if not v:
        return ""
    failed = [c for c in checks if c["state"] == "failed"]
    if failed:
        return "But a check failed - " + "; ".join(f"{c['name']}: {c['why']}" for c in failed) + "."
    if v == "not checked":
        return "Done, not checked (" + "; ".join(describe(c) for c in checks) + ")."
    return "Checks: " + "; ".join(describe(c) for c in checks) + "."
