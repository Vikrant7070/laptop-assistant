"""One data store: NOVA_DATA alone moves every file Nova keeps, and no module freezes a path at import."""
import json
import os
import subprocess
import sys
from pathlib import Path

from nova import store

ROOT = Path(__file__).resolve().parent.parent

# Run in a fresh interpreter: imports every nova module it can (the desktop stack may be missing on CI),
# then reports where the store points and every module-level Path that sits inside the data folder.
PROBE = r"""
import importlib, json, pkgutil, sys
from pathlib import Path
import nova
from nova import shared, store
imported, skipped, frozen = [], [], []
for m in pkgutil.walk_packages(nova.__path__, "nova."):
    if m.name in ("nova.__main__", "nova.core.desktop"):
        continue
    try:
        mod = importlib.import_module(m.name)
    except ImportError as e:          # a third-party package this machine does not have
        skipped.append(f"{m.name}: {e}")
        continue
    imported.append(m.name)
    for k, v in vars(mod).items():
        if isinstance(v, Path) and v != shared.DATA and v.is_relative_to(shared.DATA):
            frozen.append(f"{m.name}.{k}")
s = store.current()
print(json.dumps({"data": str(shared.DATA), "root": str(s.root),
                  "paths": {k: str(v) for k, v in s.data_paths().items()},
                  "imported": imported, "skipped": skipped, "frozen": frozen}))
"""


def _probe(data):
    env = {**os.environ, "NOVA_DATA": str(data)}
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_nova_data_alone_moves_every_file(tmp_path):
    data = tmp_path / "elsewhere"
    got = _probe(data)
    assert Path(got["root"]) == data
    outside = {k: v for k, v in got["paths"].items() if not Path(v).is_relative_to(data)}
    assert not outside, f"these files ignore NOVA_DATA: {outside}"
    assert "nova.core.app" in got["imported"], got["skipped"]       # the probe really loaded Nova


def test_no_module_freezes_a_data_path_at_import(tmp_path):
    got = _probe(tmp_path / "data")
    assert not got["frozen"], ("ask store.current() for the path when you need it, so tests and NOVA_DATA can "
                               "move it: " + ", ".join(got["frozen"]))


def test_store_paths_follow_the_installed_store(tmp_path):
    before = store.current()
    try:
        s = store.use(store.Store(tmp_path, skills_dir=tmp_path / "sk", routing=tmp_path / "r.json"))
        assert store.current() is s
        assert s.settings == tmp_path / "settings.json" and s.sfx == tmp_path / "sfx"
        assert s.skills_dir == tmp_path / "sk" and s.routing == tmp_path / "r.json"
    finally:
        store.use(before)


def test_write_json_is_whole_file_and_read_json_falls_back(tmp_path):
    s = store.Store(tmp_path)
    s.write_json(s.notes, [{"text": "milk"}])
    assert s.read_json(s.notes) == [{"text": "milk"}]
    assert not list(tmp_path.glob("*.tmp"))                       # the temp file was renamed into place
    s.notes.write_text("{broken", encoding="utf-8")
    assert s.read_json(s.notes, default=[]) == []
    assert s.read_json(tmp_path / "missing.json", default={}) == {}


def test_unknown_names_are_an_error(tmp_path):
    try:
        store.Store(tmp_path).not_a_file
    except AttributeError as e:
        assert "not_a_file" in str(e)
    else:
        raise AssertionError("expected AttributeError")
