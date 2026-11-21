"""Where Nova keeps her files: one object, one redirect point.

Every file Nova writes about the user (settings, notes, knowledge, keys, logs, learned facts...) lives under
one data folder, `data/` in the project or wherever NOVA_DATA points. Modules used to build their own path
constants at import time, so redirecting them meant patching a dozen module globals. Now they ask the
current Store when they need a path:

    from nova import store
    store.current().settings          # -> <data>/settings.json

Tests (and anything else that wants a different folder) install their own: `store.use(store.Store(tmp))`.

Two locations are deliberately not under the data folder, because they ship with the project and the user
edits them by hand: the skills folder (`skills/`, or NOVA_SKILLS) and the model routing file
(`config/model_routing.json`). They are on the Store too, so one object still answers "where is X?".
Downloaded model caches (~/.cache/nova-*) are not user data and stay where they are.
"""
import json
import os
import threading
from pathlib import Path

from nova import shared

# name -> file under the data folder
FILES = {
    "settings": "settings.json",
    "notes": "notes.json",
    "reminders": "reminders.json",
    "knowledge": "knowledge.json",
    "old_memory": "memory.json",           # the earlier flat list of facts, read once when knowledge.json is new
    "keys": "keys.json",
    "mcp": "mcp.json",
    "phone": "phone.json",
    "gmail": "gmail.json",
    "devices": "devices.json",
    "forms": "form_details.json",
    "episodes": "episodes.jsonl",
    "facts": "facts.json",
    "questions": "questions.json",
    "task_log": "task_log.txt",
    "moves": "moves.json",
    "look": "look.json",
    "voice_profile": "voice_profile.json",
    "workers": "workers.json",             # coding workers' settings (workers.py, ⚙ Workers)
    "jobs": "jobs.json",                   # coding job history (workers.py)
}
# name -> folder under the data folder
DIRS = {
    "sfx": "sfx",
    "terminal": "terminal",
    "research_chrome": "research-chrome",
    "joint_webview": "joint_check_webview",
    "job_logs": "jobs",                    # each coding job's raw output (workers.py)
}


class Store:
    """The data folder and every named file in it. Paths are computed on use, never cached elsewhere."""

    def __init__(self, root, skills_dir=None, routing=None):
        self.root = Path(root)
        self.skills_dir = Path(skills_dir or os.environ.get("NOVA_SKILLS") or shared.ROOT / "skills")
        self.routing = Path(routing or shared.ROOT / "config" / "model_routing.json")
        self.lock = threading.RLock()

    def __getattr__(self, name):
        if name in FILES:
            return self.root / FILES[name]
        if name in DIRS:
            return self.root / DIRS[name]
        raise AttributeError(f"Store has no path called {name!r}")

    def file(self, name):
        """A loose file in the data folder (generated scripts and the like)."""
        return self.root / name

    def data_paths(self):
        """Every named file and folder that must live under the data folder."""
        return {name: getattr(self, name) for name in (*FILES, *DIRS)}

    def read_json(self, path, default=None):
        """The file's JSON, or `default` when it is missing or unreadable."""
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def write_json(self, path, obj, indent=1):
        """Write the whole file at once (temp file + replace), so a crash never leaves half a file."""
        path = Path(path)
        with self.lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(obj, indent=indent, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)


_current = Store(shared.DATA)


def current():
    """The Store everything reads and writes through."""
    return _current


def use(new):
    """Install `new` (a Store) as the current one and return it."""
    global _current
    _current = new
    return new
