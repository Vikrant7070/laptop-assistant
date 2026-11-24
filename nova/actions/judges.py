"""Judges for workers' results (manager plan Phase 5): measured quality, not the worker's word.

    settings(conf)                 workers.json -> "judges", every number clamped to its range
    save(current, body)            ⚙ Workers: the judges' fields of a save, clamped
    code(cwd, env, checkpoint)  -> ([lint, build?], built folder or "")   lint/build clean when the project sets them up
    research(job, text)         -> [citations, independent, backed, disagreements]   from the saved report
    project(root, folder, test) -> the job's folder, else the nearest folder above it (up to the git root) passing test
    run(name, cwd, argv, ...)   -> a check: the command run time-limited with the worker's safety env, Stop stops it

The website judge (page, console, mobile, links, lighthouse) is `sitejudge.py`. Every check is jobchecks' shape:
{"name", "state": passed / failed / skipped, "why"} (+ "detail"). A check that could not run is skipped, with why,
never passed; nothing is installed to make a judge run. The "why" of a failed check is a sentence Nova can quote back
to the worker ("mobile layout overflows at 375 px"), so 4c-3's send-back brief carries it as it is.
"""
import json
import os
import re
import shutil
import urllib.parse
from pathlib import Path

DEFAULTS = {"page_secs": 40, "start_secs": 60, "lint_secs": 180, "build_secs": 300, "width": 375,
            "lighthouse_min": 80, "lighthouse_secs": 120, "backed_share": 0.5}
LIMITS = {"page_secs": (10, 180), "start_secs": (10, 300), "lint_secs": (10, 900), "build_secs": (30, 1800),
          "width": (320, 480), "lighthouse_min": (0, 100), "lighthouse_secs": (30, 600), "backed_share": (0.0, 1.0)}
SAVED = {"judges_page_secs": "page_secs", "judges_lighthouse_min": "lighthouse_min"}     # the fields ⚙ Workers shows


def _clamp(key, value):
    lo, hi = LIMITS[key]
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float(DEFAULTS[key])
    v = min(hi, max(lo, v))
    return v if isinstance(DEFAULTS[key], float) else int(round(v))


def settings(conf=None):
    """The judges' settings: workers.json -> "judges", missing or silly values back to the default, all in range."""
    if conf is None:
        from nova.actions import workers
        conf = workers.config()
    given = (conf or {}).get("judges") or {}
    given = given if isinstance(given, dict) else {}
    return {k: _clamp(k, given.get(k, d)) for k, d in DEFAULTS.items()}


def save(current, body):
    """The judges' part of a ⚙ Workers save: the shown fields from the body, clamped; the rest kept."""
    out = settings({"judges": current or {}})
    for field, key in SAVED.items():
        if field in (body or {}):
            try:
                float(body[field])
            except (TypeError, ValueError):
                raise ValueError(f"{field.replace('judges_', '').replace('_', ' ')} must be a number")
            out[key] = _clamp(key, body[field])
    return out


def _check(name, state, why, detail=""):
    from nova.actions import jobchecks
    return jobchecks._check(name, state, why, detail)


def project(root, folder, test):
    """The job's folder if test(folder), else the nearest folder above it, up to the git root, that passes; else the
    job's folder."""
    here, top = Path(os.path.abspath(folder or root)), Path(os.path.abspath(root or folder))
    start = here
    while True:
        if test(here):
            return here
        if here == top or top not in here.parents:
            return start
        here = here.parent


def run(name, cwd, argv, env=None, checkpoint=None, secs=None):
    """Run a judge's command like the tests (time limit, safety env, Stop) -> a check called `name`."""
    from nova.actions import jobchecks
    return jobchecks.run_tests(cwd, argv, env, checkpoint, secs, name=name)


# ------------------------------------------------------------------ code: lint and build

def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def scripts(folder):
    """package.json's scripts, {} without one (or when it can't be read)."""
    try:
        pkg = json.loads(Path(folder, "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    s = pkg.get("scripts") if isinstance(pkg, dict) else None
    return s if isinstance(s, dict) else {}


def npm(folder, script):
    """-> ([npm, "run", script], "") or (None, why it can't run). Nothing is installed."""
    if not scripts(folder).get(script):
        return None, f"package.json has no {script} script"
    tool = ("pnpm" if Path(folder, "pnpm-lock.yaml").exists() else
            "yarn" if Path(folder, "yarn.lock").exists() else "npm")
    exe = shutil.which(tool)
    if not exe:
        return None, f"{tool} isn't installed"
    if not Path(folder, "node_modules").is_dir():
        return None, "its packages aren't installed (no node_modules) - I don't install anything on my own"
    return [exe, "run", script], ""


def _venv_tool(folder, name):
    for d in (".venv", "venv", "env"):
        for p in (Path(folder, d, "Scripts", name + ".exe"), Path(folder, d, "bin", name)):
            if p.is_file():
                return str(p)
    return shutil.which(name) or ""


def _has_ruff(folder):
    return (Path(folder, "ruff.toml").exists() or Path(folder, ".ruff.toml").exists()
            or "[tool.ruff" in _read(Path(folder, "pyproject.toml")))


def _has_flake8(folder):
    return (Path(folder, ".flake8").exists() or "[flake8]" in _read(Path(folder, "setup.cfg"))
            or "[flake8]" in _read(Path(folder, "tox.ini")))


def lint_command(folder):
    """-> (argv, "") or (None, why). Only what the project itself sets up: ruff / flake8 config, an npm lint script."""
    if _has_ruff(folder):
        exe = _venv_tool(folder, "ruff")
        return ([exe, "check", "."], "") if exe else (None, "ruff is set up but isn't installed")
    if _has_flake8(folder):
        exe = _venv_tool(folder, "flake8")
        return ([exe, "."], "") if exe else (None, "flake8 is set up but isn't installed")
    if scripts(folder).get("lint"):
        return npm(folder, "lint")
    return None, "the project has no lint set up"


def _sets_up_code(folder):
    return _has_ruff(folder) or _has_flake8(folder) or Path(folder, "package.json").exists()


BUILT = ("dist", "build", "out")


def code(cwd, env=None, checkpoint=None, conf=None):
    """Lint (when set up) and, for a package.json project, its build. -> (checks, the built folder with an
    index.html, or "")."""
    s = conf or settings()
    checks, built = [], ""
    argv, why = lint_command(cwd)
    checks.append(run("lint", cwd, argv, env, checkpoint, s["lint_secs"]) if argv else _check("lint", "skipped", why))
    if Path(cwd, "package.json").exists():
        argv, why = npm(cwd, "build")
        if not argv:
            checks.append(_check("build", "skipped", why))
        else:
            c = run("build", cwd, argv, env, checkpoint, s["build_secs"])
            checks.append(c)
            if c["state"] == "passed":
                built = next((str(Path(cwd, d)) for d in BUILT if Path(cwd, d, "index.html").is_file()), "")
    return checks, built


# ------------------------------------------------------------------ research: claims, sources, disagreements

SOURCE_LINE = re.compile(r"^\s*\[(\d+)\]\s*(.*)$")
URL = re.compile(r"https?://\S+")
POINT = re.compile(r"^\s*(?:\d+[.)]|[-•*])\s+(.+)$")
MARK = re.compile(r"\[(\d+)\]")
DISAGREE = re.compile(r"\b(?:disagree\w*|conflicting|contradict\w*|sources? differ\w*)\b", re.I)


def host(url):
    h = (urllib.parse.urlsplit(str(url)).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def parse_report(text):
    """The saved report -> (findings, {n: url}, disagreements line or "").
    Findings are the numbered / bulleted points (else the sentences with a [n] mark)."""
    text = str(text or "").replace("\r\n", "\n")
    body, _, tail = text.partition("\nSources:\n")
    sources, last = {}, None
    for line in tail.splitlines():
        m = SOURCE_LINE.match(line)
        if m:
            last = int(m.group(1))
            u = URL.search(m.group(2))
            sources[last] = u.group(0) if u else ""
        elif last is not None and not sources.get(last):
            u = URL.search(line)
            if u:
                sources[last] = u.group(0)
    findings, said = [], ""
    for line in body.splitlines():
        if re.match(r"^\W*disagree\w*\s*:", line, re.I):
            said = line.strip()
            continue
        m = POINT.match(line)
        if m:
            findings.append(m.group(1).strip())
        elif not said and DISAGREE.search(line):
            said = line.strip()
    if not findings:
        findings = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", body) if MARK.search(s)]
    if not said:
        said = next((f for f in findings if DISAGREE.search(f)), "")
    return findings, sources, said


def _short(s, n=60):
    s = " ".join(MARK.sub("", s).split())
    return s if len(s) <= n else s[:n - 1] + "…"


def research(job, text=None, conf=None):
    """The research judge over the saved report: every finding cites a real source; the cited sources come from 2+
    different sites; enough findings are backed by 2+ different sites; disagreements are listed (or "none")."""
    s = conf or settings()
    if text is None:
        text = _read(job.get("path") or "")
    if not text.strip():
        why = "the report file can't be read"
        return [_check(n, "skipped", why) for n in ("citations", "independent", "backed", "disagreements")]
    findings, sources, said = parse_report(text)
    checks = []
    if not findings:
        checks.append(_check("citations", "failed", "the report has no findings to check"))
    else:
        bare = [f for f in findings if not MARK.search(f)]
        wrong = sorted({int(n) for f in findings for n in MARK.findall(f)} - set(sources))
        if wrong:
            checks.append(_check("citations", "failed", "cites " + ", ".join(f"[{n}]" for n in wrong[:4])
                                 + f", but only {len(sources)} source{'s are' if len(sources) != 1 else ' is'} listed"))
        elif bare:
            checks.append(_check("citations", "failed", f"{len(bare)} of {len(findings)} findings cite no source "
                                 f"(\"{_short(bare[0])}\")"))
        else:
            checks.append(_check("citations", "passed", f"all {len(findings)} findings cite a source"))
    cited = {n for f in findings for n in map(int, MARK.findall(f)) if n in sources}
    hosts = {host(sources[n]) for n in cited if host(sources[n])}
    every = {host(u) for u in sources.values() if host(u)}
    if len(sources) < 2:
        why = f"only {len(sources) or 'no'} source{'s' if len(sources) != 1 else ''} read"
        checks += [_check("independent", "skipped", why), _check("backed", "skipped", why),
                   _check("disagreements", "skipped", why)]
        return checks
    if len(hosts) >= 2:
        checks.append(_check("independent", "passed", f"cites {len(hosts)} different sites"))
    else:
        checks.append(_check("independent", "failed", "every cited source is from " + (next(iter(hosts)) if hosts
                             else "no site") + " - it needs 2 or more different sites"))
    if findings:
        backed = [f for f in findings if len({host(sources[int(n)]) for n in MARK.findall(f)
                                              if int(n) in sources and host(sources[int(n)])}) >= 2]
        need = s["backed_share"]
        state = "passed" if len(backed) >= need * len(findings) else "failed"
        why = f"{len(backed)} of {len(findings)} findings are backed by 2+ different sites"
        if state == "failed":
            lone = next((f for f in findings if f not in backed), "")
            why += f" (need {need:.0%}; e.g. \"{_short(lone)}\" has one)"
        checks.append(_check("backed", state, why))
    else:
        checks.append(_check("backed", "skipped", "no findings"))
    if len(every) < 2:
        checks.append(_check("disagreements", "skipped", "every source is from one site"))
        return checks
    checks.append(_check("disagreements", "passed", "listed: " + _short(said, 80)) if said else
                  _check("disagreements", "failed", "the report doesn't say where the sources disagree (or that they "
                                                    "agree)"))
    return checks
