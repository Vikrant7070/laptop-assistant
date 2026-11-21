"""Nova's task exam: how often does she really do what she's asked? (roadmap R5, review point 8)

    python -m nova.exam --list                                   # the tasks, nothing runs
    python -m nova.exam --model qwen3-vl:4b-instruct --yes       # run all 25 on THIS laptop
    python -m nova.exam --model cloud:google:gemini-2.5-flash --group files --yes

It acts on the real desktop: it opens and closes Notepad, Calculator, a YouTube tab and Settings, types, switches
dark mode (restored at the end) and writes files in its own sandbox folder. Close your work first. It runs only when
you start it with --yes (decided 2026-11-21), never on a schedule and never in CI. Quit Nova first: two Novas on one
desktop fight over the windows.

Each task (exam/tasks.toml) goes: setup → Nova answers the request (app.answer, quietly) → the success checks →
teardown. Recorded per task: seconds, steps (tool calls), what she claimed (done / not done / asked), what the checks
saw, and the outcome:

    success          the checks pass (or, for a task that can't be done, she said she couldn't)
    false_done       she said it's done, but a check failed (or she claimed an impossible task)
    honest_failure   it isn't done, and she said so
    asked            she asked for an OK on a task not marked approve=true, so it stopped there
    error            Nova or the exam itself crashed

Results go to exam/results/<date>-<model>.json and a .md summary: success rate, false "done" rate, honest failure
rate, median seconds per task.
"""
import argparse
import datetime as dt
import json
import os
import re
import shlex
import socket
import statistics
import sys
import tempfile
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from nova import shared

TASKS_FILE = shared.ROOT / "exam" / "tasks.toml"
RESULTS_DIR = shared.ROOT / "exam" / "results"
GROUPS = ("apps", "windows", "files", "settings", "questions", "multi", "impossible")
ACTIONS = {"close_app", "close_window", "start", "dark_mode", "file", "wait"}
EXAM_CHECKS = {"no_window", "reply_has", "reply_lacks", "reply_matches", "file_exists", "file_missing", "file_has",
               "dir_exists"}
MAX_APPROVALS = 3

# How she says she didn't do it. Read from her reply when no Judge verdict says otherwise.
NOT_DONE_RE = re.compile(
    r"\b(can'?t|cannot|couldn'?t|could not|unable|not able|wasn'?t able|didn'?t|did not|don'?t have|doesn'?t exist|"
    r"does not exist|no such|isn'?t installed|not installed|not find|couldn'?t find|can'?t find|failed|"
    r"i don'?t think i finished|not possible|no way to|sorry)\b", re.I)


@dataclass
class Task:
    id: str
    group: str
    request: str
    setup: list = field(default_factory=list)
    success: list = field(default_factory=list)
    teardown: list = field(default_factory=list)
    possible: bool = True
    approve: bool = False


@dataclass
class Result:
    id: str
    group: str
    request: str
    outcome: str
    claimed: str              # done / not done / asked / none
    checks: list              # [{"check": 'window_open "Notepad"', "ok": bool, "seen": str}]
    seconds: float
    steps: int
    approvals: int = 0
    reply: str = ""
    error: str = ""


# ------------------------------------------------------------------ loading


def parse_line(line):
    """'file_has "list.txt" "milk"' -> ("file_has", ["list.txt", "milk"]); 'wait 2' -> ("wait", ["2"])."""
    parts = shlex.split(str(line), posix=True)
    if not parts:
        raise ValueError("empty line")
    return parts[0], parts[1:]


def load(path=TASKS_FILE):
    """The exam's tasks, checked: unknown keys, groups, checks or actions are errors (a typo must not pass)."""
    from nova.actions import checks
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    tasks, seen = [], set()
    known = set(Task.__dataclass_fields__)
    for t in raw.get("task", []):
        extra = set(t) - known
        if extra:
            raise ValueError(f"task {t.get('id')!r}: unknown keys {sorted(extra)}")
        task = Task(**t)
        if not task.id or task.id in seen:
            raise ValueError(f"task id {task.id!r} is empty or repeated")
        seen.add(task.id)
        if task.group not in GROUPS:
            raise ValueError(f"task {task.id}: group {task.group!r} isn't one of {GROUPS}")
        if task.possible and not task.success:
            raise ValueError(f"task {task.id}: a possible task needs success checks")
        for line in task.success:
            name, args = parse_line(line)
            if name not in checks.NAMES | EXAM_CHECKS or name == "result_has":
                raise ValueError(f"task {task.id}: unknown check {name!r}")
            if not args and name != "fullscreen":
                raise ValueError(f"task {task.id}: check {name!r} needs an argument")
        for line in task.setup + task.teardown:
            name, _ = parse_line(line)
            if name not in ACTIONS:
                raise ValueError(f"task {task.id}: unknown action {name!r}")
        tasks.append(task)
    return tasks


# ------------------------------------------------------------------ judging one task


def claimed(out, verdict=None):
    """What Nova said about her own work: 'asked', 'done' or 'not done'."""
    if out.get("pending"):
        return "asked"
    if out.get("stopped") or out.get("error"):
        return "not done"
    if verdict is not None and verdict.get("verdict") is False:
        return "not done"            # the Judge's check failed and she was told so
    return "not done" if NOT_DONE_RE.search(out.get("reply") or "") else "done"


def outcome(task, claim, checks_ok):
    if claim == "asked":
        return "asked"
    if not task.possible:
        return "success" if claim == "not done" else "false_done"
    if checks_ok:
        return "success"
    return "false_done" if claim == "done" else "honest_failure"


def run_task(task, *, answer, approve, check, act, sandbox, clock=time.monotonic):
    """One task through setup → answer → checks → teardown. Nothing here raises."""
    request = task.request.replace("{sandbox}", str(sandbox))
    start, out, approvals, error, verdict = clock(), {}, 0, "", None
    try:
        for line in task.setup:
            act(*parse_line(line), sandbox=sandbox)
        out, _status = answer(request)
        while out.get("pending") and task.approve and approvals < MAX_APPROVALS:
            approvals += 1
            out, _status = approve()
        verdict = out.get("_verdict")
    except Exception as e:                        # intentional: one crashing task is recorded, the exam goes on
        error = f"{type(e).__name__}: {e}"
    seconds = round(clock() - start, 1)
    results = []
    if not error:
        for line in task.success:
            name, args = parse_line(line)
            try:
                ok, seen = check(name, args, reply=out.get("reply") or "", sandbox=sandbox)
            except Exception as e:                # intentional: a check that crashes counts as failed
                ok, seen = False, f"couldn't check ({type(e).__name__}: {e})"
            results.append({"check": line, "ok": bool(ok), "seen": str(seen)[:200]})
    for line in task.teardown:
        try:
            act(*parse_line(line), sandbox=sandbox)
        except Exception:
            pass                                  # intentional: teardown is best effort; the next setup starts clean
    claim = claimed(out, verdict) if not error else "none"
    result = outcome(task, claim, all(r["ok"] for r in results)) if not error else "error"
    return Result(task.id, task.group, request, result, claim, results, seconds,
                  steps=len(out.get("activity") or []), approvals=approvals, reply=(out.get("reply") or "")[:500],
                  error=error)


def summary(results):
    n = len(results) or 1
    count = {k: sum(r.outcome == k for r in results) for k in ("success", "false_done", "honest_failure", "asked", "error")}
    impossible = [r for r in results if r.group == "impossible"]
    return {
        "tasks": len(results),
        **count,
        "success_rate": round(count["success"] / n, 3),
        "false_done_rate": round(count["false_done"] / n, 3),
        "honest_failure_rate": round(count["honest_failure"] / n, 3),
        "impossible_honest": f"{sum(r.outcome == 'success' for r in impossible)}/{len(impossible)}",
        "median_seconds": statistics.median(r.seconds for r in results) if results else 0,
    }


def report_md(meta, summ, results):
    lines = [f"# Task exam: {meta['model']}, {meta['date']}", "",
             f"Commit `{meta.get('commit', '?')}`, {summ['tasks']} tasks"
             + (f" (groups: {', '.join(meta['groups'])})" if meta.get("groups") else "") + ".", "",
             "| Success | False \"done\" | Honest failure | Asked | Error | Impossible, honest | Median s/task |",
             "|---|---|---|---|---|---|---|",
             f"| {summ['success_rate']:.0%} ({summ['success']}) | {summ['false_done_rate']:.0%} ({summ['false_done']}) | "
             f"{summ['honest_failure_rate']:.0%} ({summ['honest_failure']}) | {summ['asked']} | {summ['error']} | "
             f"{summ['impossible_honest']} | {summ['median_seconds']} |", "",
             "| Task | Outcome | Claimed | Checks | s | Steps |", "|---|---|---|---|---|---|"]
    for r in results:
        checks_txt = "; ".join(("✅ " if c["ok"] else "❌ ") + c["check"] for c in r.checks) or (r.error or "—")
        lines.append(f"| {r.id} | {r.outcome} | {r.claimed} | {checks_txt.replace('|', '/')} | {r.seconds} | {r.steps} |")
    return "\n".join(lines) + "\n"


def save(results, meta, folder=RESULTS_DIR):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9.]+", "-", meta["model"].lower()).strip("-")
    base = folder / f"{meta['date']}-{slug}"
    summ = summary(results)
    base.with_suffix(".json").write_text(json.dumps({"meta": meta, "summary": summ, "results": [asdict(r) for r in results]},
                                                    indent=1, ensure_ascii=False), encoding="utf-8")
    base.with_suffix(".md").write_text(report_md(meta, summ, results), encoding="utf-8")
    return base


# ------------------------------------------------------------------ the real desktop (only used by main)


def _sandbox_path(sandbox, name):
    p = (Path(sandbox) / name).resolve()
    if Path(sandbox).resolve() not in p.parents:
        raise ValueError(f"{name!r} is outside the exam sandbox")
    return p


def desktop_act(name, args, sandbox):
    """Setup/teardown actions on the real laptop."""
    import subprocess
    from nova.actions import tools
    if name == "wait":
        time.sleep(float(args[0]))
    elif name == "close_app":
        import psutil
        target = args[0].lower().removesuffix(".exe")
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower().removesuffix(".exe") == target:
                p.terminate()
        time.sleep(0.5)
    elif name == "close_window":
        tools.windows.window_action(args[0], "close")
    elif name == "start":
        subprocess.Popen([args[0]], shell=False)
    elif name == "dark_mode":
        tools.system.set_dark_mode(args[0].lower() == "on")
    elif name == "file":
        p = _sandbox_path(sandbox, args[0])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args[1] if len(args) > 1 else "", encoding="utf-8")


def desktop_check(name, args, reply, sandbox):
    """One success check: the skill checks on the real desktop, or the exam's own."""
    from nova.actions import checks
    arg = args[0] if args else ""
    if name == "reply_has":
        return arg.lower() in reply.lower(), "her reply"
    if name == "reply_lacks":
        return arg.lower() not in reply.lower(), "her reply"
    if name == "reply_matches":
        return bool(re.search(arg, reply, re.I)), "her reply"
    if name == "no_window":
        ok, seen = checks.evaluate({"check": "window_open", "arg": arg}, timeout=0)
        return (not ok), seen
    if name in ("file_exists", "file_missing", "file_has", "dir_exists"):
        p = _sandbox_path(sandbox, arg)
        if name == "file_exists":
            return p.is_file(), f"{p.name} {'exists' if p.is_file() else 'is missing'}"
        if name == "file_missing":
            return not p.exists(), f"{p.name} {'still exists' if p.exists() else 'is gone'}"
        if name == "dir_exists":
            return p.is_dir(), f"{p.name} {'exists' if p.is_dir() else 'is missing'}"
        text = p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else ""
        return (args[1].lower() in text.lower()), (text[:80] or "no such file")
    return checks.evaluate({"check": name, "arg": arg})


def nova_running(port=5055):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _commit():
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=shared.ROOT, capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except Exception:
        return "?"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m nova.exam", description="Nova's task exam (acts on this laptop).")
    ap.add_argument("--model", help="the brain to test, e.g. qwen3-vl:4b-instruct or cloud:google:gemini-2.5-flash")
    ap.add_argument("--group", action="append", choices=GROUPS, help="only these groups (repeatable)")
    ap.add_argument("--task", action="append", help="only these task ids (repeatable)")
    ap.add_argument("--list", action="store_true", help="show the tasks and exit")
    ap.add_argument("--yes", action="store_true", help="really run it on this laptop")
    args = ap.parse_args(argv)

    tasks = load()
    if args.group:
        tasks = [t for t in tasks if t.group in args.group]
    if args.task:
        tasks = [t for t in tasks if t.id in args.task]
    if args.list or not args.yes:
        for t in tasks:
            print(f"{t.group:<11} {t.id:<24} {t.request}")
        if not args.list:
            print(f"\n{len(tasks)} tasks. This acts on your real desktop (apps, typing, dark mode, files in a sandbox).\n"
                  "Close your work, quit Nova, then run again with --yes (and --model).")
            return 2
        return 0
    if not args.model:
        ap.error("--model is required to run the exam")
    if nova_running():
        print("Nova is running (port 5055). Quit her from the tray first: two Novas fight over the desktop.")
        return 2

    from nova.core import app
    old = app.settings()
    s = dict(old, model=args.model, voice=False)
    app.save_settings(s)
    sandbox = Path(tempfile.mkdtemp(prefix="nova-exam-"))
    dark_before = desktop_check("dark_mode", ["on"], "", sandbox)[0]
    meta = {"model": args.model, "date": dt.date.today().isoformat(), "commit": _commit(),
            "groups": args.group or [], "sandbox": str(sandbox), "python": sys.version.split()[0]}

    def answer(text):
        with app.session.lock:
            app.session.state["messages"].clear()
            app.session.state["pending"] = None
        out, status = app.answer(text, quiet=True)
        out["_verdict"] = (app.session.state.get("task") or {}).get("verdict")
        return out, status

    def approve():
        out, status = app.confirm_pending(True, quiet=True)
        return (out if isinstance(out, dict) else {"reply": str(out)}), status

    results = []
    try:
        for i, task in enumerate(tasks, 1):
            print(f"[{i}/{len(tasks)}] {task.id}: {task.request}", flush=True)
            r = run_task(task, answer=answer, approve=approve, check=desktop_check, act=desktop_act, sandbox=sandbox)
            print(f"    -> {r.outcome} ({r.claimed}, {r.seconds} s)" + (f" {r.error}" if r.error else ""), flush=True)
            results.append(r)
    finally:
        app.save_settings(old)
        try:
            desktop_act("dark_mode", ["on" if dark_before else "off"], sandbox)
        except Exception as e:
            print(f"Couldn't restore dark mode: {e}")
    base = save(results, meta)
    summ = summary(results)
    print(f"\nSuccess {summ['success_rate']:.0%}, false 'done' {summ['false_done_rate']:.0%}, honest failures "
          f"{summ['honest_failure_rate']:.0%}, median {summ['median_seconds']} s/task.\nSaved {base}.json and .md")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
