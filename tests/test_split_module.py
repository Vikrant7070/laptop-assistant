"""scripts/split_module.py on a toy module: it moves statements whole and rewrites only cross-module names."""
import importlib
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("split_module", ROOT / "scripts" / "split_module.py")
split_module = importlib.util.module_from_spec(spec)
sys.modules["split_module"] = split_module
spec.loader.exec_module(split_module)

TOY = '''"""A toy module."""
import re

WORD = re.compile("hi")      # used by greet (other module)


def greet(text):
    return bool(WORD.search(text)) and shout(text)


class Base:
    LOUD = 2


# a comment that moves with Child
class Child(Base):
    def go(self, text):
        return shout(text)


def shout(text):
    return text.upper() * Base.LOUD


def rename(word):
    global WORD
    WORD = re.compile(word)
'''
MAP = {"package": "toypkg", "ranges": {"1": "words", "7": "talk", "11": "shapes", "24": "talk"},
       "names": {"shout": "shapes"}}


@pytest.fixture
def toy(tmp_path, monkeypatch):
    src = tmp_path / "toypkg.py"
    src.write_text(TOY, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield src
    for name in [m for m in sys.modules if m == "toypkg" or m.startswith("toypkg.")]:
        del sys.modules[name]


def write(src, cfg):
    p = split_module.plan(src, cfg)
    out = src.with_suffix("")
    out.mkdir()
    for name, text in split_module.render(p, cfg).items():
        (out / name).write_text(text, encoding="utf-8")
    src.unlink()
    return p, out


def test_split_works_and_patches_reach_callers(toy):
    p, out = write(toy, MAP)
    assert p["load_deps"]["talk"] == set() and p["run_deps"]["talk"] == {"words", "shapes"}   # bottom imports
    pkg = importlib.import_module("toypkg")
    assert pkg.greet("hi there") == "HI THEREHI THERE"
    assert pkg.Child().go("a") == "AA" and pkg.home_of("Child").__name__ == "toypkg.shapes"
    pkg.rename("bye")                                           # `global WORD` now assigns words.WORD
    assert pkg.WORD.pattern == "bye" and pkg.words.WORD.pattern == "bye"
    talk = (out / "talk.py").read_text(encoding="utf-8")
    assert "words.WORD.search(text)" in talk and "shapes.shout(text)" in talk
    shapes = (out / "shapes.py").read_text(encoding="utf-8")
    assert "# a comment that moves with Child\nclass Child(Base):" in shapes
    assert "global WORD" not in talk and "    words.WORD = re.compile(word)" in talk
    assert "import re" in talk and "import re" not in shapes        # each module gets only the imports it uses


def test_refuses_a_module_name_a_local_would_shadow(toy):
    cfg = {**MAP, "ranges": {"1": "text", "7": "talk", "11": "shapes", "24": "talk"}}
    with pytest.raises(split_module.SplitError, match="shadowed by a local named 'text'"):
        split_module.plan(toy, cfg)


def test_refuses_a_name_defined_in_two_modules(toy, tmp_path):
    toy.write_text(TOY + "\n\nWORD = None\n", encoding="utf-8")
    cfg = {**MAP, "ranges": {**MAP["ranges"], "30": "talk"}}
    with pytest.raises(split_module.SplitError, match="defined in both"):
        split_module.plan(toy, cfg)


def test_voice_section_map_still_plans_cleanly():
    """The map that made nova/audio/voice/ (kept for the record): every module it names exists."""
    import tomllib
    cfg = tomllib.loads((ROOT / "scripts" / "splits" / "voice.toml").read_text(encoding="utf-8"))
    names = set(cfg["ranges"].values()) | set(cfg["names"].values())
    assert {p.stem for p in (ROOT / "nova" / "audio" / "voice").glob("*.py")} - {"__init__"} == names
