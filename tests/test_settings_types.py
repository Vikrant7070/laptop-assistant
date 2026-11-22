"""Typed settings (roadmap R7): every key of data/settings.json has a type and a default in config.Settings, a broken
value can't crash or silently change behaviour, unknown keys survive, and a guard fails on a key the code reads that
the dataclass doesn't know."""
import ast
import json
from pathlib import Path

from nova import store
from nova.brain import router
from nova.core.app import config

ROOT = Path(__file__).resolve().parent.parent

# What settings() returned before R7 (the defaults dict that was written out by hand), so the move changed nothing.
OLD_DEFAULTS = {
    "model": config.DEFAULT_MODEL, "think": False, "autonomy": "careful", "camera": False, "screen": False,
    "voice": True, "wake": False, "provider": "ollama", "cloud_models": {}, "notifications": False,
    "notif_sleep": False, "deep_sleep": True, "listen_pause": 0.7, "listen_max": 10, "mic_level": 5,
    "notif_muted": [], "phone_calls": True, "email_cloud": False, "mobile": False, "phone_quiet": True,
    "phone_notes_cloud": False, "suggest": False, "reaction_sounds": True, "power_ok": False, "reasoning": "auto",
    "react_to_sound": True, "avatar_roam": "corner", "presence": False, "presence_lock": False, "error_help": True,
    "skill_screen_checks": False, "skill_teach": True, "barge_in": True, "character": "nova",
}


def _write(data):
    store.current().settings.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


def _read():
    return json.loads(store.current().settings.read_text(encoding="utf-8"))


def test_the_defaults_are_the_old_ones():
    _write({})
    s = config.settings()
    assert {k: s[k] for k in OLD_DEFAULTS} == OLD_DEFAULTS
    assert s["schema"] == config.SCHEMA and s["phone_banners_hidden"] is False


def test_round_trip_keeps_every_value():
    _write({})
    s = config.settings()
    s.update(autonomy="full", listen_pause=1.2, cloud_models={"gemini": "flash"}, notif_muted=["Slack"],
             character="makima", auto_route=False)
    config.save_settings(s)
    assert config.settings() == s
    assert _read() == s


def test_a_broken_file_gives_defaults_and_keeps_unknown_keys(monkeypatch):
    logged = []
    monkeypatch.setattr(config, "log_exception", lambda where, exc=None: logged.append((where, str(exc))))
    _write({"autonomy": "reckless", "voice": "yes", "listen_max": 3.5, "mic_level": True, "cloud_models": [],
            "listen_pause": 1, "camera": True, "heavy_steps": 2, "my_own_key": {"a": 1}})
    s = config.settings()
    assert s["autonomy"] == "careful" and s["voice"] is True and s["listen_max"] == 10 and s["mic_level"] == 5
    assert s["cloud_models"] == {}
    assert s["listen_pause"] == 1.0 and isinstance(s["listen_pause"], float)    # an int is fine for a float
    assert s["camera"] is True                                                  # good values are kept
    assert s["heavy_steps"] == 2 and s["my_own_key"] == {"a": 1}                # unknown keys survive, top level
    assert s["model"] == config.DEFAULT_MODEL                                   # a missing key gets its default
    assert {w for w, _ in logged} == {f"settings.json: {k}" for k in
                                      ("autonomy", "voice", "listen_max", "mic_level", "cloud_models")}
    assert any("'reckless'" in e and "'careful'" in e for _, e in logged)


def test_a_file_that_isnt_json_or_an_object_gives_the_defaults(monkeypatch):
    logged = []
    monkeypatch.setattr(config, "log_exception", lambda where, exc=None: logged.append(where))
    for text in ("{not json", "[1, 2]", "null"):
        _write(text)
        s = config.settings()
        assert {k: s[k] for k in OLD_DEFAULTS} == OLD_DEFAULTS
    assert logged == ["settings.json"] * 3


def test_save_settings_validates_before_writing(monkeypatch):
    monkeypatch.setattr(config, "log_exception", lambda where, exc=None: None)
    config.save_settings({"autonomy": "everything", "think": 1, "routing_extra": 5})
    on_disk = _read()
    assert on_disk["autonomy"] == "careful" and on_disk["think"] is False      # never written as given
    assert on_disk["routing_extra"] == 5
    assert on_disk["voice"] is True and on_disk["schema"] == config.SCHEMA


def test_settings_hands_out_copies():
    _write({"notif_muted": ["Teams"]})
    a = config.settings()
    a["notif_muted"].append("Slack")
    a["cloud_models"]["openai"] = "x"
    assert config.settings()["notif_muted"] == ["Teams"] and config.settings()["cloud_models"] == {}


def test_a_migration_runs_for_an_older_schema(monkeypatch):
    monkeypatch.setattr(config, "SCHEMA", 2)
    monkeypatch.setitem(config.MIGRATIONS, 1, lambda raw: {**{k: v for k, v in raw.items() if k != "listen"},
                                                           "listen_max": raw.get("listen", 10)})
    _write({"schema": 1, "listen": 20})
    s = config.settings()
    assert s["listen_max"] == 20 and "listen" not in s and s["schema"] == 2


def test_routing_overrides_get_no_default():
    """router.options: a key in settings.json overrides data/model_routing.json, so these must stay absent unless
    the user set them (a default in Settings would silently override the file)."""
    _write({})
    s = config.settings()
    assert not set(router.ROUTING_KEYS) & set(s)
    assert not set(router.ROUTING_KEYS) & set(config._FIELDS)


# ------------------------------------------------------------------ the key guard

def _is_settings_call(node):
    if isinstance(node, ast.Call) and not node.args:
        f = node.func
        return (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")) == "settings"
    if isinstance(node, ast.BoolOp):                    # s = s or settings()
        return any(_is_settings_call(v) for v in node.values)
    return False


def _own_nodes(fn):
    """The nodes of a function, without the nested functions and classes (they are scopes of their own)."""
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        yield node
        stack.extend(c for c in ast.iter_child_nodes(node)
                     if not isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)))


def settings_keys_read():
    """{key: [where]} for every constant key read from settings(): settings()["x"], settings().get("x"), and the
    same through a local name that is only ever assigned from settings() in its function."""
    found = {}
    files = sorted((ROOT / "nova").rglob("*.py")) + [ROOT / "desktop.py"]
    for path in files:
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in [tree, *(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]:
            nodes = list(_own_nodes(fn))
            assigned = {}
            for n in nodes:
                targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(
                    n, (ast.AnnAssign, ast.AugAssign, ast.For, ast.NamedExpr)) else []
                for t in targets:
                    if isinstance(t, ast.Name):
                        assigned.setdefault(t.id, []).append(isinstance(n, ast.Assign) and _is_settings_call(n.value))
            holders = {name for name, how in assigned.items() if all(how)}

            def holds(v):
                return _is_settings_call(v) or (isinstance(v, ast.Name) and v.id in holders)
            for n in nodes:
                key = None
                if isinstance(n, ast.Subscript) and holds(n.value) and isinstance(n.slice, ast.Constant):
                    key = n.slice.value
                elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("get", "pop")
                      and holds(n.func.value) and n.args and isinstance(n.args[0], ast.Constant)):
                    key = n.args[0].value
                if isinstance(key, str):
                    found.setdefault(key, []).append(f"{path.relative_to(ROOT)}:{n.lineno}")
    return found


def test_every_settings_key_the_code_reads_is_in_the_dataclass():
    allowed = set(config._FIELDS) | set(router.ROUTING_KEYS)
    found = settings_keys_read()
    assert len(found) >= 30, "the scan stopped finding settings reads - fix the guard"
    unknown = {k: where for k, where in found.items() if k not in allowed}
    assert not unknown, ("add these keys to config.Settings (type + default) or stop reading them:\n  "
                         + "\n  ".join(f"{k}: {', '.join(w[:3])}" for k, w in sorted(unknown.items())))


def test_the_guard_catches_an_unknown_key(tmp_path, monkeypatch):
    pkg = tmp_path / "nova"
    pkg.mkdir()
    (pkg / "bad.py").write_text("from nova.core.app import config\n\n\ndef f():\n    s = config.settings()\n"
                                "    return s.get('not_a_setting'), config.settings()['wake']\n", encoding="utf-8")
    monkeypatch.setattr(__import__(__name__), "ROOT", tmp_path)
    assert set(settings_keys_read()) == {"not_a_setting", "wake"}
