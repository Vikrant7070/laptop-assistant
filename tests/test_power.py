"""The ⚙ 'shut down without asking' flag, and the tidy-up (close windows, clear notifications) before shutdown."""
from nova.core import app
from nova.actions import tools


def _set(**kw):
    s = app.settings()
    s.update(kw)
    app.save_settings(s)


def test_shutdown_asks_by_default():
    _set(autonomy="careful", power_ok=False)
    assert app.is_risky({"name": "power_action", "args": {"action": "shutdown"}})
    _set(autonomy="trusted")
    assert app.is_risky({"name": "power_action", "args": {"action": "shutdown"}})


def test_power_ok_flag_skips_the_question(brain):
    _set(autonomy="careful", power_ok=True)
    assert not app.is_risky({"name": "power_action", "args": {"action": "shutdown"}})
    assert not app.is_risky({"name": "power_action", "args": {"action": "restart"}})
    assert app.is_risky({"name": "kill_process", "args": {"name_or_pid": "x"}})   # only power is affected
    fake, ran = brain([("", [("power_action", {"action": "shutdown"})]), ("Shutting down.", [])])
    out, _ = app.answer("shut down the laptop")
    assert ran == [("power_action", {"action": "shutdown"})] and not out.get("pending")
    _set(power_ok=False)


def test_setup_saves_the_flag():
    c = app.app.test_client()
    c.post("/api/setup", json={"power_ok": True})
    assert app.settings()["power_ok"] is True
    assert c.get("/api/status").get_json()["power_ok"] is True
    c.post("/api/setup", json={"power_ok": False})
    assert app.settings()["power_ok"] is False


def test_shutdown_closes_windows_and_clears_notifications_first(monkeypatch):
    order = []
    monkeypatch.setattr(tools, "close_all_windows", lambda: order.append("windows") or "Closed 3 windows.")
    monkeypatch.setattr(tools, "_clear_notifications", lambda: order.append("notes") or "Cleared your notifications.")
    monkeypatch.setattr(tools.subprocess, "Popen", lambda cmd, **k: order.append(cmd))
    out = tools.power_action("shutdown")
    assert order == ["windows", "notes", "shutdown /s /t 30"]
    assert "Closed 3 windows" in out and "30 seconds" in out


def test_sleep_and_cancel_do_not_tidy(monkeypatch):
    monkeypatch.setattr(tools, "close_all_windows", lambda: (_ for _ in ()).throw(AssertionError("closed windows")))
    monkeypatch.setattr(tools.subprocess, "Popen", lambda cmd, **k: None)
    tools.power_action("cancel")
    tools.power_action("sleep")


def test_close_all_windows_spares_nova_and_the_shell(monkeypatch):
    monkeypatch.setattr(tools, "_windows", lambda: [(1, "Nova", "pythonw.exe"), (2, "Docs - Word", "WINWORD.EXE"),
                                                    (3, "Downloads", "explorer.exe"), (4, "Shell", "explorer.exe")])
    pids = {1: 100, 2: 200, 3: 300, 4: 300}
    classes = {3: "CabinetWClass", 4: "Shell_TrayWnd"}
    monkeypatch.setattr(tools, "_own_pids", lambda: {100})

    def get_pid(hwnd, ref):
        ref._obj.value = pids[hwnd]
    monkeypatch.setattr(tools._user32, "GetWindowThreadProcessId", get_pid)

    def get_class(hwnd, buf, n):
        buf.value = classes.get(hwnd, "")
    monkeypatch.setattr(tools._user32, "GetClassNameW", get_class)
    closed = []
    monkeypatch.setattr(tools._user32, "PostMessageW", lambda h, m, w, l: closed.append(h))
    assert tools.close_all_windows() == "Closed 2 windows."
    assert closed == [2, 3]
