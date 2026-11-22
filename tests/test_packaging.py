"""Packaging (roadmap R4): pyproject's dependency lists and the exact pins in requirements*.txt can't drift apart."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
LOCKS = ["requirements.txt", "requirements-test.txt", "requirements-dev.txt"]


def _name(req):
    return re.split(r"[\s<>=!~\[;]", req.strip(), maxsplit=1)[0].lower().replace("_", "-")


def declared():
    names = {_name(r) for r in PROJECT["dependencies"]}
    for reqs in PROJECT["optional-dependencies"].values():
        names |= {_name(r) for r in reqs if not r.startswith("nova[")}
    return names


def pins():
    out = {}
    for lock in LOCKS:
        for line in (ROOT / lock).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                assert "==" in line, f"{lock}: {line!r} is not an exact pin"
                out.setdefault(_name(line), lock)
    return out


def test_every_pin_is_declared_in_pyproject():
    missing = {name: lock for name, lock in pins().items() if name not in declared()}
    assert not missing, f"pinned but not in pyproject dependencies/extras: {missing}"


def test_every_declared_package_has_a_pin():
    unpinned = declared() - set(pins())
    assert not unpinned, f"in pyproject but not pinned in {LOCKS}: {sorted(unpinned)}"


def test_extras_named_in_the_roadmap():
    extras = PROJECT["optional-dependencies"]
    assert {"voice", "gpu", "camera", "cloud", "test", "dev"} <= set(extras)
    assert "faster-whisper" in extras["voice"] and "anthropic" in extras["cloud"]
