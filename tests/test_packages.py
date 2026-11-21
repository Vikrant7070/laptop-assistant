"""Guards for every module that was split into a package (docs/adr/0003, scripts/split_module.py).

Each package answers `pkg.<name>` from the name's home module, so callers didn't change. But a patch or assignment on
the package itself would only shadow the name: the module that really uses it would never see it (a test's fake would
test nothing). For every package in PACKAGES these tests check that nothing outside patches or assigns a moved name on
the package, that every `pkg.<name>` used anywhere still resolves, and that every module stays under 800 lines.
Add a package here when you split one.
"""
import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from nova.actions import checks, tools
from nova.audio import voice
from nova.core import app

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted((ROOT / "nova").rglob("*.py")) + sorted((ROOT / "tests").glob("*.py")) + [ROOT / "desktop.py"]
MAX_LINES = 800


@dataclass
class Package:
    module: object
    aliases: tuple              # names callers hold it under ("app" for tools means app.tools.<name>)
    callers: re.Pattern         # a file that matches this holds the package under one of the aliases
    test_only: frozenset = frozenset()   # pkg.<x> references that aren't names (file names in comments, test helpers)

    @property
    def folder(self):
        return Path(self.module.__file__).parent

    @property
    def name(self):
        return self.module.__name__


PACKAGES = [
    Package(tools, ("tools", "app.tools"), re.compile(r"from nova\.actions import .*\btools\b|\bapp\.tools\b"),
            frozenset({"py"})),
    Package(app, ("app", "server"), re.compile(r"from nova\.core import app\b"),
            frozenset({"banner_calls", "py", "app"})),
    Package(voice, ("voice", "app.voice"), re.compile(r"from nova\.audio import .*\bvoice\b|\bapp\.voice\b"),
            frozenset({"py", "RealMouth"})),
]
IDS = [p.name for p in PACKAGES]


def _callers(pkg):
    for path in SOURCES:
        if pkg.folder in path.parents or not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if pkg.callers.search(text):
            yield path, text


def _is_alias(node, pkg):
    """`tools`, or `app.tools`: the package under one of its names."""
    for alias in pkg.aliases:
        parts = alias.split(".")
        if len(parts) == 1 and isinstance(node, ast.Name) and node.id == parts[0]:
            return True
        if (len(parts) == 2 and isinstance(node, ast.Attribute) and node.attr == parts[1]
                and isinstance(node.value, ast.Name) and node.value.id == parts[0]):
            return True
    return False


@pytest.mark.parametrize("pkg", PACKAGES, ids=IDS)
def test_nothing_patches_or_assigns_a_moved_name_on_the_package(pkg):
    bad = []
    for path, text in _callers(pkg):
        for node in ast.walk(ast.parse(text)):
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "setattr" and len(node.args) >= 2
                    and _is_alias(node.args[0], pkg) and isinstance(node.args[1], ast.Constant)
                    and pkg.module.home_of(node.args[1].value)):
                bad.append(f"{path.name}:{node.lineno} setattr(<{pkg.name}>, {node.args[1].value!r})")
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    if isinstance(t, ast.Attribute) and _is_alias(t.value, pkg) and pkg.module.home_of(t.attr):
                        bad.append(f"{path.name}:{node.lineno} <{pkg.name}>.{t.attr} = ...")
    assert not bad, ("patch / assign it in its home module (home_of(name)), or the code that uses it never sees it:"
                     "\n  " + "\n  ".join(bad))


@pytest.mark.parametrize("pkg", PACKAGES, ids=IDS)
def test_every_name_used_by_callers_still_resolves(pkg):
    ref = re.compile(r"(?<![\w.])(?:" + "|".join(re.escape(a) for a in pkg.aliases) + r")\.([A-Za-z_]\w*)")
    missing = sorted({f"{path.name}: {n}" for path, text in _callers(pkg) for n in ref.findall(text)
                      if n not in pkg.test_only and not hasattr(pkg.module, n)})
    assert not missing, missing


@pytest.mark.parametrize("pkg", PACKAGES, ids=IDS)
def test_modules_stay_small(pkg):
    big = {p.name: n for p in pkg.folder.glob("*.py") if (n := len(p.read_text(encoding="utf-8").splitlines())) > MAX_LINES}
    assert not big, f"split these further: {big}"


@pytest.mark.parametrize("pkg", PACKAGES, ids=IDS)
def test_every_home_name_resolves_to_its_home(pkg):
    for name, mod in pkg.module._HOME.items():
        assert getattr(pkg.module, name) is getattr(mod, name), name


# ------------------------------------------------------------------ per package: a home patch reaches every caller


def test_tools_patch_on_the_home_module_reaches_every_caller(monkeypatch):
    fake = [(1, "Spotify Premium", "Spotify.exe")]
    monkeypatch.setattr(tools.windows, "_windows", lambda: fake)
    assert tools._windows() == fake                                   # through the package
    assert checks.window_open("spotify", {})[0] is True               # checks.py calls tools._windows()
    assert "Spotify Premium" in tools.windows.list_windows()          # the home module's own callers


def test_every_tool_lives_in_one_area_module():
    for name, fn in tools.FUNCS.items():
        module = fn.__module__
        assert module.startswith("nova.actions.tools.") or module == "nova.actions.speedup", (name, module)
        if module.startswith("nova.actions.tools."):
            assert tools.home_of(name).__name__ == module


def test_app_patch_on_the_home_module_reaches_every_caller(monkeypatch):
    s = dict(app.settings(), autonomy="full")
    monkeypatch.setattr(app.config, "settings", lambda: s)
    assert app.settings()["autonomy"] == "full"                          # through the package
    assert not app.is_risky({"name": "kill_process", "args": {}})        # prompts/session read config.settings()


def test_the_server_starts_from_the_package():
    assert (Path(app.__file__).parent / "__main__.py").exists()           # python -m nova.core.app
    client = app.app.test_client()
    assert client.get("/").status_code == 200 and client.get("/api/status").status_code == 200


def test_voice_set_wake_reaches_every_module(monkeypatch):
    for mod, name in [(voice.whisper, "NOVA_NAMES"), (voice.whisper, "WAKE_RE"), (voice.whisper, "WAKE_HINT"),
                      (voice.whisper, "WAKE_NAME"), (voice.barge_in, "BARGE_RE")]:
        monkeypatch.setattr(mod, name, getattr(mod, name))             # restored after the test
    voice.set_wake(r"(?:lucy|lucie)", "Hey Lucy.", "Lucy")
    assert voice.WAKE_RE.search("hey lucy") and voice.whisper.WAKE_RE is voice.WAKE_RE
    assert voice.WAKE_NAME == "Lucy" and voice.barge_in.BARGE_RE.search("lucy stop")


def test_voice_fake_mouth_is_what_the_app_uses():
    assert voice.Mouth is voice.speak.Mouth                              # conftest put FakeMouth in its home
    assert type(app.voice_io.mouth).__name__ == "FakeMouth"
