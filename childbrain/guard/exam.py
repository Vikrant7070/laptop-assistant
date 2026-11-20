"""The nightly exam: a fixed set of tests the brain never practises on.

Every practice game is tested on its *exam* worlds (split="test"), and the three sealed games are tested
too. Same worlds, same moves for the same brain, so a brain's exam score is exactly repeatable.

The guard runs the exam in a separate process inside a Windows job, with a time limit:
    python -m childbrain.guard.exam <version folder>      (prints the result as JSON)
Only the brain's *data* (genes + weights, plain JSON) goes in; the network code is the fixed,
tripwire-checked code in childbrain/brain/. So a brain can't change how it is marked.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from .. import ROOT
from .jobs import Job

EXAM_COPIES = 128
EXAM_SECONDS = 300
CREATE_NO_WINDOW = 0x08000000


def suite() -> list[str]:
    from ..worlds import PRACTICE, SEALED

    return list(PRACTICE) + list(SEALED)


def suite_fingerprint() -> str:
    from .seals import SEAL_FILE

    h = hashlib.sha256(json.dumps([suite(), EXAM_COPIES]).encode())
    h.update(SEAL_FILE.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def examine(folder: Path, copies: int = EXAM_COPIES) -> dict:
    """Run the exam in this process. Use run_exam() from the guard instead."""
    import torch

    from ..brain.net import BadBrain, act, from_json
    from ..worlds import make, play_once

    torch.set_num_threads(2)  # be gentle with the laptop
    start = time.time()
    folder = Path(folder)
    out = {"items": {}, "total": None, "broken": None, "suite": suite_fingerprint()}
    try:
        genes = json.loads((folder / "genes.json").read_text(encoding="utf-8"))
        weights = json.loads((folder / "weights.json").read_text(encoding="utf-8"))
        net = from_json(genes, weights).eval()
        for name in suite():
            world = make(name, n=copies, split="test")
            out["items"][name] = round(play_once(world, lambda obs, allowed: act(net, obs, allowed)).mean().item(), 6)
        out["total"] = round(sum(out["items"].values()), 6)
    except BadBrain as e:
        out["broken"] = str(e)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as e:
        out["broken"] = f"can't be loaded or played: {type(e).__name__}: {str(e)[:200]}"
    out["seconds"] = round(time.time() - start, 2)
    return out


def run_exam(folder: Path, timeout: float = EXAM_SECONDS) -> dict:
    """Run the exam in its own sandboxed process and return the result."""
    proc = subprocess.Popen([sys.executable, "-W", "ignore", "-m", "childbrain.guard.exam", str(folder)], cwd=ROOT,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    job = Job(proc)
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        job.kill()
        proc.communicate()
        return {"items": {}, "total": None, "broken": f"the exam took longer than {timeout:.0f} s", "suite": suite_fingerprint()}
    finally:
        job.close()
    lines = [line for line in stdout.decode("utf-8", "replace").splitlines() if line.startswith("{")]
    if proc.returncode != 0 or not lines:
        return {"items": {}, "total": None, "broken": f"the exam crashed (exit {proc.returncode})", "suite": suite_fingerprint()}
    return json.loads(lines[-1])


if __name__ == "__main__":
    print(json.dumps(examine(Path(sys.argv[1]))))
