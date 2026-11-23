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

def files_inside(root, folder, files):
    """The files the worker changed (relative to the git root) must all be inside the folder the job was given."""
    if not files:
        return _check("files", "skipped", "no files changed")
    try:
        rel = os.path.relpath(os.path.abspath(folder), os.path.abspath(root))
    except ValueError:                       # another drive (a subst / mapped spelling of the same place)
        rel = "."
    if rel in (".", "") or rel.startswith(".."):     # git's root spelled differently (short names): can't tell apart
        rel = "."
    if rel == ".":
        return _check("files", "passed", f"{len(files)} changed, all inside the project folder")
    prefix = rel.replace("\\", "/").rstrip("/") + "/"
    outside = [f for f in files if not str(f).replace("\\", "/").startswith(prefix)]
    if outside:
        return _check("files", "failed", f"changed files outside {rel}: " + ", ".join(outside[:5])
                      + (f" and {len(outside) - 5} more" if len(outside) > 5 else ""))
    return _check("files", "passed", f"{len(files)} changed, all inside {rel}")


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


def run_tests(cwd, argv, env=None, checkpoint=None, secs=None):
    """Run the test command; the job's Stop stops it too. -> a "tests" check."""
    secs = secs or TEST_SECS
    shown = " ".join([os.path.basename(argv[0])] + argv[1:]).replace("python.exe -m ", "").replace("python -m ", "")
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), env={**(env or os.environ), "CI": "1", "PYTHONDONTWRITEBYTECODE": "1"}, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        return _check("tests", "skipped", f"{shown} couldn't start ({e.strerror or e})")
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
                return _check("tests", "failed", f"{shown} ran longer than {secs // 60 or 1} min, so I stopped it",
                              "".join(out))
            time.sleep(0.2)
    except BaseException:
        if proc.poll() is None:
            _kill(proc)
        raise
    reader.join(5)
    text = "".join(out)
    if proc.returncode == 0:
        return _check("tests", "passed", f"{shown}: {_failing(text)}")
    if proc.returncode == 5 and "pytest" in shown:            # pytest: no tests were collected
        return _check("tests", "skipped", "the project has no tests yet")
    return _check("tests", "failed", f"{shown}: {_failing(text)}", text)


def coding(root, folder, files, env=None, checkpoint=None):
    """The checks of a finished coding job. Never raises (a check that crashes is skipped, with why)."""
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
    return checks


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
    return checks


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
