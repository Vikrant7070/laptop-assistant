"""Guard v1: starts the brain as a separate program and answers its requests.

  - Before starting: the off switch must be off, the policy lock and the exam seals must match, and the
    brain's code must pass the tripwire check (codecheck.py). Otherwise nothing starts.
  - The brain runs in its own process inside a Windows job (jobs.py), with no window.
  - It talks to the guard only through a private named pipe, in plain JSON (never pickle, so a message
    can't run code inside the guard). Every request is approved or refused against the policy and logged.
  - Off switch: a STOP file in the runs folder (`python -m childbrain.guard stop`). The guard checks for
    it 20 times a second and ends the brain's whole job at once. It stays off until a person clears it.
  - Limits: actions per minute, run time, disk, copies, request size, and too many refused requests in
    a row of trying. Breaking the run-time or refusal limit stops the brain.

The guard is the brain's only way to do anything besides thinking: saving files, notes, playing games.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from importlib.util import find_spec
from multiprocessing.connection import Client, Listener
from pathlib import Path

from .. import ROOT, RUNS_DIR
from . import codecheck, policy as policy_mod, seals
from .jobs import Job
from .log import ChainLog

STOP_NAME = "STOP"
NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CREATE_NO_WINDOW = 0x08000000


def stop_file(runs_dir: Path = RUNS_DIR) -> Path:
    return Path(runs_dir) / STOP_NAME


def off_switch(runs_dir: Path = RUNS_DIR) -> Path:
    """Turn the off switch on: every guard using this runs folder stops its brain within a second."""
    path = stop_file(runs_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n", encoding="utf-8")
    return path


def clear_off_switch(runs_dir: Path = RUNS_DIR) -> bool:
    path = stop_file(runs_dir)
    if path.exists():
        path.unlink()
        return True
    return False


@dataclass
class Summary:
    folder: str
    target: str
    stopped: str = ""                       # why the run ended
    exit_code: int | None = None
    stop_ms: float | None = None            # off switch / limit: how long until the brain was gone
    contained: bool = False                 # brain ran inside a Windows job
    allowed: dict = field(default_factory=dict)
    blocked: list = field(default_factory=list)
    problems: list = field(default_factory=list)


class Guard:
    def __init__(self, policy: dict | None = None, runs_dir: Path = RUNS_DIR):
        self.locked = policy is None  # the real rule book must match its lock; test rule books are given directly
        self.policy = policy_mod.load() if policy is None else policy
        self.limits = self.policy["limits"]
        self.actions = policy_mod.allowed_actions(self.policy)
        self.runs_dir = Path(runs_dir)
        self.hello = threading.Event()

    # ----- before starting -----

    def preflight(self, target: str) -> list[str]:
        problems = []
        if stop_file(self.runs_dir).exists():
            problems.append("the off switch is on (python -m childbrain.guard clear)")
        if self.locked:
            problems += policy_mod.verify()
        problems += seals.verify()
        file = _target_file(target)
        if file is None:
            problems.append(f"can't find brain {target!r} (must be childbrain.<module>:<function>)")
        elif not codecheck.has_brain(file, target.partition(":")[2]):
            problems.append(f"{target} isn't a function marked @brain")
        else:
            problems += codecheck.check(file)
        return problems

    # ----- one guarded run -----

    def run(self, target: str) -> Summary:
        self.folder = _new_folder(self.runs_dir, target)
        self.files = self.folder / "brain_files"
        self.files.mkdir()
        self.log = ChainLog(self.folder / "guard_log.jsonl")
        self.summary = Summary(folder=str(self.folder), target=target)
        self.allowed, self.blocked = Counter(), []
        self.rate = deque()
        self.stop_reason = ""
        self.steps_granted = 0
        self.connected = threading.Event()
        self.log.write(who="guard", event="start", target=target, level=self.policy["level"],
                       policy=policy_mod.fingerprint(self.policy))

        problems = self.preflight(target)
        if problems:
            self.summary.stopped, self.summary.problems = "refused to start", problems
            self.log.write(who="guard", event="refused", problems=problems)
            return self._finish()

        address = rf"\\.\pipe\childbrain-guard-{secrets.token_hex(8)}"
        key = secrets.token_bytes(32)
        listener = Listener(address, family="AF_PIPE", authkey=key)
        env = {**os.environ, "CHILDBRAIN_GUARD_PIPE": address, "CHILDBRAIN_GUARD_KEY": key.hex()}
        env.pop("CHILDBRAIN_RUN_FOLDER", None)
        out = (self.folder / "brain_output.txt").open("wb")
        proc = subprocess.Popen([sys.executable, "-m", "childbrain.brainhost", target], cwd=ROOT, env=env,
                                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        job = Job(proc)
        self.summary.contained = job.contained
        self.log.write(who="guard", event="brain started", pid=proc.pid, contained=job.contained)
        server = threading.Thread(target=self._serve, args=(listener,), daemon=True)
        server.start()
        started = time.monotonic()
        try:
            while True:
                if stop_file(self.runs_dir).exists():
                    self._stop(job, "off switch", since=stop_file(self.runs_dir).stat().st_mtime)
                    break
                if self.stop_reason:
                    self._stop(job, self.stop_reason)
                    break
                if time.monotonic() - started > self.limits["run_seconds"]:
                    self._stop(job, "run time limit")
                    break
                if proc.poll() is not None:
                    self.summary.stopped = "brain finished"
                    break
                time.sleep(0.05)
        finally:
            if proc.poll() is None:
                self._stop(job, self.summary.stopped or "guard ended")
            self.summary.exit_code = proc.poll()
            job.close()
            out.close()
            if not self.connected.is_set():  # the pipe server still waits for a brain: wake it so it can end
                try:
                    Client(address, family="AF_PIPE", authkey=key).close()
                except OSError:
                    pass
            listener.close()
            server.join(2)
        self.log.write(who="guard", event="end", stopped=self.summary.stopped, exit_code=self.summary.exit_code,
                       stop_ms=self.summary.stop_ms)
        return self._finish()

    def _stop(self, job: Job, reason: str, since: float | None = None) -> None:
        t0 = time.time()
        job.kill()
        job.proc.wait(5)
        gone = time.time()
        self.summary.stopped = reason
        self.summary.stop_ms = round((gone - (since if since is not None else t0)) * 1000, 1)
        self.log.write(who="guard", event="stopped brain", reason=reason, stop_ms=self.summary.stop_ms)

    def _finish(self) -> Summary:
        self.summary.allowed = dict(self.allowed)
        self.summary.blocked = self.blocked
        (self.folder / "guard_summary.json").write_text(json.dumps(asdict(self.summary), indent=2) + "\n",
                                                        encoding="utf-8")
        return self.summary

    # ----- requests -----

    def _serve(self, listener: Listener) -> None:
        try:
            conn = listener.accept()
        except OSError:
            return
        finally:
            self.connected.set()
        with conn:
            while True:
                try:
                    raw = conn.recv_bytes(maxlength=self.limits["request_bytes"])
                except (EOFError, OSError) as e:
                    if not isinstance(e, EOFError) and not self.summary.stopped:
                        self.stop_reason = "request too large or broken pipe"
                        self.log.write(who="guard", event="bad pipe", error=str(e)[:200])
                    return
                reply = self.handle(raw)
                try:
                    conn.send_bytes(json.dumps(reply).encode())
                except OSError:
                    return

    def handle(self, raw: bytes) -> dict:
        try:
            req = json.loads(raw)
            action, args, why = req["action"], req.get("args", {}), req.get("why", {})
            if not (isinstance(action, str) and isinstance(args, dict) and isinstance(why, dict)):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return self._verdict("?", {}, {}, False, "bad request: not {action, args, why} JSON")
        if self.stop_reason:
            return self._verdict(action, args, why, False, "the guard is stopping")
        if action not in self.actions:
            level = self.policy["levels"][str(self.policy["level"])]["name"]
            return self._verdict(action, args, why, False,
                                 f"not on the allowed list for trust level {self.policy['level']} ({level})")
        now = time.monotonic()
        while self.rate and now - self.rate[0] > 60:
            self.rate.popleft()
        if len(self.rate) >= self.limits["actions_per_minute"]:
            return self._verdict(action, args, why, False, "too many actions per minute")
        self.rate.append(now)
        try:
            ok, reason, result = getattr(self, f"_do_{action}")(args)
        except (KeyError, TypeError, ValueError) as e:
            ok, reason, result = False, f"bad arguments: {e}", None
        return self._verdict(action, args, why, ok, reason, result)

    def _verdict(self, action, args, why, ok, reason, result=None) -> dict:
        self.log.write(who="brain", action=action, args=_short(args), why=_short(why),
                       verdict="allowed" if ok else "blocked", reason=reason)
        if ok:
            self.allowed[action] += 1
        else:
            self.blocked.append({"action": action, "reason": reason})
            if len(self.blocked) >= self.limits["blocked_before_stop"] and not self.stop_reason:
                self.stop_reason = "too many blocked actions"
        return {"ok": ok, "reason": reason, "result": result}

    # ----- the allowed actions (level 0: sandbox) -----

    def _do_hello(self, args):
        self.hello.set()
        return True, "hello", {"level": self.policy["level"], "actions": sorted(self.actions), "limits": self.limits}

    def _do_play(self, args):
        from ..worlds import CHANGES, PRACTICE, SEALED

        game, copies, steps = args["game"], int(args.get("copies", 1)), int(args.get("steps", 0))
        change = args.get("change")
        if game in SEALED:
            return False, "sealed exam game: exams are run by the guard, never practised", None
        if game not in PRACTICE:
            return False, f"no such game {game!r}", None
        if change not in CHANGES:
            return False, f"no such change {change!r}", None
        if args.get("split", "train") != "train":
            return False, "exam worlds are for the guard's exams only", None
        if not 1 <= copies <= self.limits["max_copies"]:
            return False, f"copies must be 1..{self.limits['max_copies']}", None
        if not 0 <= steps <= self.limits["max_steps_per_play"]:
            return False, f"steps must be 0..{self.limits['max_steps_per_play']}", None
        self.steps_granted += copies * steps
        return True, "ok", {"granted_steps": steps}

    def _do_save(self, args):
        name, data = args["name"], args["data"]
        if not isinstance(name, str) or not NAME_OK.match(name):
            return False, "file name not allowed (letters, digits, . _ - only; no folders)", None
        raw = base64.b64decode(data, validate=True) if args.get("base64") else str(data).encode("utf-8")
        target = self.files / name
        used = sum(p.stat().st_size for p in self.files.iterdir()) - (target.stat().st_size if target.exists() else 0)
        if used + len(raw) > self.limits["disk_mb"] * 1024 * 1024:
            return False, f"disk limit ({self.limits['disk_mb']} MB)", None
        target.write_bytes(raw)
        return True, "saved", {"bytes": len(raw)}

    def _do_log(self, args):
        text = args["text"]
        if not isinstance(text, str) or len(text) > self.limits["log_text_chars"]:
            return False, f"notes must be text of at most {self.limits['log_text_chars']} characters", None
        return True, "noted", None

    def _do_done(self, args):
        return True, "bye", None


def _short(d: dict) -> dict:
    """What goes in the log: no big blobs."""
    out = {}
    for k, v in d.items():
        if isinstance(v, str) and len(v) > 120:
            out[k] = f"<{len(v)} characters>"
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = f"<{type(v).__name__}>"
    return out


def _target_file(target: str) -> Path | None:
    module, _, fn = target.partition(":")
    if not module.startswith("childbrain.") or not fn or module.startswith("childbrain.guard"):
        return None
    try:
        spec = find_spec(module)
    except (ImportError, ValueError):
        return None
    return Path(spec.origin) if spec and spec.origin else None


def _new_folder(runs_dir: Path, target: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", target.rpartition(":")[2]) or "brain"
    base = f"{time.strftime('%Y%m%d-%H%M%S')}_guarded_{safe}"
    for i in range(1000):
        folder = Path(runs_dir) / (base if i == 0 else f"{base}-{i}")
        try:
            folder.mkdir(parents=True)
            return folder
        except FileExistsError:
            continue
    raise RuntimeError("couldn't make a run folder")
