"""Phone Link's own pop-ups are hidden while Nova reads notifications, and come back when she stops."""
from nova.actions import notifications
from nova.core import app

real_phone_banners = notifications.phone_banners     # taken at import, before conftest's per-test stub


class FakeReg:
    HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_DWORD = "HKCU", 1, 2, 4

    def __init__(self, keys):
        self.keys = keys                 # name -> {value: data}

    def OpenKey(self, root, path, *a):
        return path

    def CloseKey(self, k):
        pass

    def EnumKey(self, k, i):
        names = list(self.keys)
        if i >= len(names):
            raise OSError
        return names[i]

    def _vals(self, k):
        return self.keys[k.rsplit("\\", 1)[1]]

    def QueryValueEx(self, k, name):
        v = self._vals(k)
        if name not in v:
            raise OSError
        return v[name], self.REG_DWORD

    def SetValueEx(self, k, name, _r, _t, data):
        self._vals(k)[name] = data

    def DeleteValue(self, k, name):
        del self._vals(k)[name]


def test_hides_only_phone_link_banners_and_restores_them():
    reg = FakeReg({"Microsoft.YourPhone_8wekyb3d8bbwe!YourPhoneCalling": {},
                   "Microsoft.YourPhone_8wekyb3d8bbwe!YourPhoneNotifications_net.whatsapp.WhatsApp": {},
                   "Microsoft.Outlook_8wekyb3d8bbwe!App": {}})
    assert real_phone_banners(False, _winreg=reg) == 2
    assert all(v.get("ShowBanner") == 0 for n, v in reg.keys.items() if "YourPhone" in n)
    assert reg.keys["Microsoft.Outlook_8wekyb3d8bbwe!App"] == {}
    assert real_phone_banners(False, _winreg=reg) == 0          # already hidden
    assert real_phone_banners(True, _winreg=reg) == 2
    assert all("ShowBanner" not in v for v in reg.keys.values())


def test_sync_hides_while_reading_and_restores_only_what_she_hid():
    app.save_settings({**app.settings(), "notifications": True})
    app.sync_phone_banners()
    assert app.banner_calls == [False] and app.settings()["phone_banners_hidden"] is True
    app.save_settings({**app.settings(), "notifications": False})
    app.sync_phone_banners()
    assert app.banner_calls == [False, True] and app.settings()["phone_banners_hidden"] is False
    app.sync_phone_banners()                                     # she never hid them now: leave the user's own setting
    assert app.banner_calls == [False, True]


def test_setting_switch_from_the_dialog(client, monkeypatch):
    monkeypatch.setattr(app.notif_watcher, "start", lambda: None)     # no real PowerShell listener
    client.post("/api/setup", json={"notifications": True, "phone_quiet": True})
    assert app.banner_calls[-1] is False
    client.post("/api/setup", json={"phone_quiet": False})
    assert app.banner_calls[-1] is True
    assert client.get("/api/status").get_json()["phone_quiet"] is False


def test_real_voice_carries_voice_only_flag():
    import queue
    from nova.audio import voice
    m = voice.RealMouth.__new__(voice.RealMouth)
    m.q = queue.Queue()
    m.say("Rani is calling.", show=False)
    m.say("Hello.")
    assert m.q.get()[2] is False and m.q.get()[2] is True
