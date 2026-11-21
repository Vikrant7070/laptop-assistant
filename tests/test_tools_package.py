"""nova/actions/tools/ is a package of modules by area. `tools.<name>` reads the name from its home module, so
callers never changed - but a patch or assignment on `tools` itself would only shadow the name and be ignored by the
module that really uses it. These tests keep that from creeping back in."""
import ast
import re
from pathlib import Path

from nova.actions import checks, tools

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "nova" / "actions" / "tools"
SOURCES = sorted((ROOT / "nova").rglob("*.py")) + sorted((ROOT / "tests").glob("*.py"))
TOOLS_REF = re.compile(r"(?<![\w.])(?:app\.)?tools\.([A-Za-z_]\w*)")


def _is_tools(node):
    return (isinstance(node, ast.Name) and node.id == "tools") or (
        isinstance(node, ast.Attribute) and node.attr == "tools" and isinstance(node.value, ast.Name)
        and node.value.id == "app")


def test_nothing_patches_or_assigns_a_moved_name_on_the_package():
    bad = []
    for path in SOURCES:
        if PACKAGE in path.parents:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "setattr" and len(node.args) >= 2
                    and _is_tools(node.args[0]) and isinstance(node.args[1], ast.Constant)
                    and tools.home_of(node.args[1].value)):
                bad.append(f"{path.name}:{node.lineno} setattr(tools, {node.args[1].value!r})")
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    if isinstance(t, ast.Attribute) and _is_tools(t.value) and tools.home_of(t.attr):
                        bad.append(f"{path.name}:{node.lineno} tools.{t.attr} = ...")
    assert not bad, ("patch the home module instead (tools.home_of(name)), or the code that uses the name never "
                     "sees it:\n  " + "\n  ".join(bad))


def test_every_tools_name_used_anywhere_still_resolves():
    missing = set()
    for path in SOURCES:
        if PACKAGE in path.parents:
            continue
        for name in TOOLS_REF.findall(path.read_text(encoding="utf-8")):
            if name != "py" and not hasattr(tools, name):              # "tools.py" in old comments is a file name
                missing.add(f"{path.name}: tools.{name}")
    assert not missing, sorted(missing)


def test_modules_stay_small():
    big = {p.name: n for p in PACKAGE.glob("*.py") if (n := len(p.read_text(encoding="utf-8").splitlines())) > 800}
    assert not big, f"split these further: {big}"


def test_a_patch_on_the_home_module_reaches_every_caller(monkeypatch):
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
