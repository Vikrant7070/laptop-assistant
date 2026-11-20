"""The guard's rule book: which actions each trust level allows, and the limits.

policy.json holds the rules; policy.lock holds its SHA-256 fingerprint. The guard won't start if they
don't match, so the rules can't be changed quietly. Only a person re-locks, after reading the change:
    python -m childbrain.guard lock
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

POLICY_FILE = Path(__file__).with_name("policy.json")
LOCK_FILE = Path(__file__).with_name("policy.lock")


def fingerprint(policy: dict) -> str:
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load(path: Path = POLICY_FILE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def lock(path: Path = POLICY_FILE, lock_file: Path = LOCK_FILE) -> str:
    fp = fingerprint(load(path))
    Path(lock_file).write_text(fp + "\n", encoding="utf-8")
    return fp


def verify(path: Path = POLICY_FILE, lock_file: Path = LOCK_FILE) -> list[str]:
    if not Path(lock_file).exists():
        return ["policy.lock is missing"]
    if fingerprint(load(path)) != Path(lock_file).read_text(encoding="utf-8").strip():
        return ["policy.json changed since it was locked"]
    return []


def allowed_actions(policy: dict) -> set[str]:
    return set(policy["levels"][str(policy["level"])]["actions"])
