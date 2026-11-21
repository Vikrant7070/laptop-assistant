"""Saved versions of the brain, kept by the guard (never by the brain).

    childbrain/runs/versions/
      0001/  genes.json  weights.json  card.json     every version is kept, so any day can be restored
      0002/  ...
      state.json      which version is in use ("current"), the history of kept versions, failure count
      nightly_log.jsonl   every nightly decision, hash-chained like the guard log
      report.txt      one line per night ("examined 1, kept 0003, rolled back: no")

card.json holds each file's SHA-256, so a version that was changed after it was saved is noticed.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .. import RUNS_DIR

FILES = ("genes.json", "weights.json")


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path: Path, data) -> None:
    Path(path).write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")


class VersionStore:
    def __init__(self, root: Path = RUNS_DIR / "versions"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_file = self.root / "state.json"

    # ----- state -----

    def state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return {"current": None, "kept": [], "fails_in_a_row": 0, "level_drops": 0}

    def save_state(self, state: dict) -> None:
        _write(self.state_file, state)

    @property
    def current(self) -> str | None:
        return self.state()["current"]

    # ----- versions -----

    def ids(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and p.name.isdigit())

    def path(self, vid: str) -> Path:
        return self.root / vid

    def card(self, vid: str) -> dict:
        return json.loads((self.path(vid) / "card.json").read_text(encoding="utf-8"))

    def update_card(self, vid: str, **changes) -> dict:
        card = {**self.card(vid), **changes}
        _write(self.path(vid) / "card.json", card)
        return card

    def submit(self, genes: dict, weights: dict, source: str = "", note: str = "") -> str:
        """Store a new candidate version. It is only used after it passes a nightly exam."""
        ids = self.ids()
        vid = f"{int(ids[-1]) + 1 if ids else 1:04d}"
        folder = self.path(vid)
        folder.mkdir()
        _write(folder / "genes.json", genes)
        _write(folder / "weights.json", weights)
        _write(folder / "card.json", {
            "version": vid, "status": "candidate", "submitted": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": source, "note": note[:200], "files": {f: _sha(folder / f) for f in FILES},
        })
        return vid

    def intact(self, vid: str) -> bool:
        """Are the version's files exactly as saved?"""
        folder = self.path(vid)
        try:
            files = self.card(vid)["files"]
            return all((folder / f).exists() and _sha(folder / f) == files[f] for f in FILES)
        except (OSError, KeyError, ValueError):
            return False

    def candidates(self) -> list[str]:
        return [v for v in self.ids() if self.card(v).get("status") == "candidate"]
