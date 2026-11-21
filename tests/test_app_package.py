"""nova/core/app/ is a package of modules by job. `app.<name>` reads the name from its home module, so desktop.py and
the tests barely changed - but a patch or assignment on `app` itself would only shadow the name, and the module that
really uses it would never see it (quitting from the tray, a test's fake settings). These tests keep that out."""
import ast
import re
from pathlib import Path

from nova.core import app

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "nova" / "core" / "app"
# The files that hold the package under a name: tests (`app`) and the desktop shell (`server`).
CALLERS = sorted((ROOT / "tests").glob("*.py")) + [ROOT / "nova" / "core" / "desktop.py"]
ALIASES = {"app", "server"}
REF = re.compile(r"(?<![\w.])(?:app|server)\.([A-Za-z_]\w*)")


def test_nothing_patches_or_assigns_a_moved_name_on_the_package():
    bad = []
    for path in CALLERS:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "setattr" and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Name) and node.args[0].id in ALIASES
                    and isinstance(node.args[1], ast.Constant) and app.home_of(node.args[1].value)):
                bad.append(f"{path.name}:{node.lineno} setattr({node.args[0].id}, {node.args[1].value!r})")
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in ALIASES
                            and app.home_of(t.attr)):
                        bad.append(f"{path.name}:{node.lineno} {t.value.id}.{t.attr} = ...")
    assert not bad, ("patch / assign it in its home module (app.home_of(name)), or the code that uses it never sees "
                     "it:\n  " + "\n  ".join(bad))


def test_every_app_name_used_by_callers_still_resolves():
    test_only = {"banner_calls", "py"}    # conftest's own list on the package; "app.py" in comments is a file name
    missing = sorted({f"{p.name}: {n}" for p in CALLERS for n in REF.findall(p.read_text(encoding="utf-8"))
                      if n not in test_only and not hasattr(app, n)})
    assert not missing, missing


def test_modules_stay_small():
    big = {p.name: n for p in PACKAGE.glob("*.py") if (n := len(p.read_text(encoding="utf-8").splitlines())) > 800}
    assert not big, f"split these further: {big}"


def test_a_patch_on_the_home_module_reaches_every_caller(monkeypatch):
    s = dict(app.settings(), autonomy="full")
    monkeypatch.setattr(app.config, "settings", lambda: s)
    assert app.settings()["autonomy"] == "full"                          # through the package
    assert not app.is_risky({"name": "kill_process", "args": {}})        # prompts/session read config.settings()


def test_the_server_starts_from_the_package():
    assert (PACKAGE / "__main__.py").exists()                             # python -m nova.core.app
    client = app.app.test_client()
    assert client.get("/").status_code == 200 and client.get("/api/status").status_code == 200
