"""Fingerprints (SHA-256) for the sealed exam games.

For each sealed game, sealed.json keeps two fingerprints:
  code  the game's own file plus the shared files it is built from
  exam  the exact exam it produces: a fixed batch of exam worlds played with fixed moves on the CPU,
        hashing every picture, score and "done" (so a changed rule or a changed exam layout shows up
        even if nobody touched the file listed under "code")

`verify()` lists every mismatch; the guard (WP 0.5) refuses to run exams when the list isn't empty.
Only a person re-seals, after looking at the change:  python -m childbrain seal
"""

from __future__ import annotations

import ctypes
import hashlib
import inspect
import json
from pathlib import Path

import torch

from .. import ROOT

SEAL_FILE = Path(__file__).with_name("sealed.json")
SHARED = ("base.py", "enemies.py", "maze.py")  # files every sealed game may depend on
EXAM_COPIES, EXAM_STEPS = 64, 60


def _file_hash(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(set(paths)):
        h.update(p.relative_to(ROOT).as_posix().encode() + b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def code_fingerprint(cls) -> str:
    here = Path(__file__).parent
    return _file_hash([Path(inspect.getsourcefile(cls))] + [here / f for f in SHARED])


def exam_fingerprint(cls) -> str:
    """Play the fixed exam on the CPU with fixed random moves and hash everything it shows."""
    world = cls(n=EXAM_COPIES, split="test", device="cpu")
    moves = torch.Generator().manual_seed(0)
    h = hashlib.sha256()
    h.update(_bytes(world.observe()))
    for _ in range(EXAM_STEPS):
        allowed = world.allowed()
        noise = torch.rand(allowed.shape, generator=moves)
        res = world.step(torch.where(allowed, noise, -1.0).argmax(1))
        for t in (res.obs, res.reward, res.done.float()):
            h.update(_bytes(t))
    return h.hexdigest()


def _bytes(t: torch.Tensor) -> bytes:
    """The tensor's raw memory (NumPy isn't installed, and .tolist() is far too slow)."""
    t = t.detach().cpu().contiguous()
    return ctypes.string_at(t.data_ptr(), t.numel() * t.element_size())


def fingerprints() -> dict:
    from . import SEALED

    return {name: {"code": code_fingerprint(cls), "exam": exam_fingerprint(cls)} for name, cls in SEALED.items()}


def seal(path: Path = SEAL_FILE) -> dict:
    prints = fingerprints()
    path.write_text(json.dumps(prints, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return prints


def verify(path: Path = SEAL_FILE) -> list[str]:
    """Every way the sealed games differ from their saved fingerprints (empty list = all good)."""
    if not path.exists():
        return [f"{path.name} is missing"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    now = fingerprints()
    problems = [f"{name}: not sealed" for name in sorted(set(now) - set(saved))]
    problems += [f"{name}: sealed but no longer a sealed game" for name in sorted(set(saved) - set(now))]
    for name in sorted(set(now) & set(saved)):
        for part in ("code", "exam"):
            if now[name][part] != saved[name].get(part):
                problems.append(f"{name}: {part} changed")
    return problems
