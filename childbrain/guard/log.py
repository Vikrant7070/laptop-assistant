"""The guard's log: one JSON line per event, each line chained to the one before by a SHA-256 hash.

Only the guard writes it. If any line is changed, removed or re-ordered later, `check_chain` notices.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

START = "0" * 64


def _digest(prev: str, entry: dict) -> str:
    body = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev + body).encode()).hexdigest()


class ChainLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.prev, self.seq = START, 0
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():  # carry on an existing log's chain
            entries = read(self.path)
            if entries:
                self.prev, self.seq = entries[-1]["hash"], entries[-1]["seq"] + 1

    def write(self, **entry) -> dict:
        with self.lock:
            entry = {"seq": self.seq, "time": round(time.time(), 3), **entry}
            entry["hash"] = _digest(self.prev, entry)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
            self.prev, self.seq = entry["hash"], self.seq + 1
            return entry


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def check_chain(path: Path) -> list[str]:
    """Every place where the log was tampered with (empty list = intact)."""
    problems, prev = [], START
    for i, entry in enumerate(read(path)):
        claimed = entry.pop("hash", None)
        if entry.get("seq") != i:
            problems.append(f"line {i}: out of order or missing lines")
        if claimed != _digest(prev, entry):
            problems.append(f"line {i}: changed")
        prev = claimed or ""
    return problems
