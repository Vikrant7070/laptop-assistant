"""speed-up-laptop: the memory check and the startup-app switch (nova/actions/speedup.py). No real registry writes."""
from pathlib import Path

from nova.actions import skills, speedup, tools


def _fake(monkeypatch, names):
    entries = [{"name": n, "hive": 0, "sub": "Run", "value": n, "where": "you", "on": True} for n in names]
    written = []
    monkeypatch.setattr(speedup, "_entries", lambda: entries)
    monkeypatch.setattr(speedup, "_write", lambda e, on: written.append((e["name"], on)))
    return written


def test_extras_turns_off_only_unneeded_apps(monkeypatch):
    written = _fake(monkeypatch, ["Steam", "JioSphereUpdaterTask", "Nova", "SecurityHealth", "Ollama", "Tailscale",
                                  "Logitech Download Assistant"])
    out = speedup.set_startup_app("extras", False)
    assert {n for n, _ in written} == {"Steam", "JioSphereUpdaterTask", "Logitech Download Assistant"}
    assert "Turned off" in out


def test_never_turns_off_nova_or_windows_security(monkeypatch):
    written = _fake(monkeypatch, ["Nova", "SecurityHealth"])
    assert "Left on" in speedup.set_startup_app("nova", False)
    speedup.set_startup_app("SecurityHealth", False)
    assert written == []


def test_admin_entries_are_reported_not_hidden(monkeypatch):
    _fake(monkeypatch, ["Logi Download Assistant"])

    def denied(e, on):
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(speedup, "_write", denied)
    assert "need admin" in speedup.set_startup_app("extras", False)


def test_extras_cannot_turn_everything_on(monkeypatch):
    written = _fake(monkeypatch, ["Steam"])
    assert "only turns apps off" in speedup.set_startup_app("extras", True)
    assert written == []


def test_tools_are_registered_and_guarded():
    assert {"check_memory", "list_startup_apps", "set_startup_app"} <= set(tools.FUNCS)
    assert "set_startup_app" in tools.RISKY and "check_memory" not in tools.RISKY
    assert "Memory in use" in tools.run_tool("check_memory", {})


def test_skill_parses_and_asks_first():
    path = Path(__file__).resolve().parents[1] / "skills" / "speed-up-laptop.md"
    s = skills.parse(path.read_text(encoding="utf-8"))
    assert s["kind"] == "routine" and not s["error"]
    assert skills.compute_risk(s["tools"]) == "consequential"     # clean_temp_files: asks before running


def test_every_startup_change_is_in_the_task_log(monkeypatch):
    from nova import store
    log = store.current().task_log
    before = log.read_text(encoding="utf-8") if log.exists() else ""
    _fake(monkeypatch, ["Steam", "Nova"])
    speedup.set_startup_app("steam", False)
    speedup.set_startup_app("nova", False)
    new = (log.read_text(encoding="utf-8") if log.exists() else "")[len(before):]
    assert "SPEEDUP startup   off: Steam" in new
    assert "SPEEDUP startup   kept (protected): Nova" in new
