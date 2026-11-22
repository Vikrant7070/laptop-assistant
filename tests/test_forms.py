"""📝 Forms and logins: Nova fills forms from details kept on the laptop, never hands the values to her brain,
never types passwords or one-time codes, and never submits by herself."""
import pytest

from nova.actions import forms, tools
from nova.core import app

DETAILS = {"full_name": "Asha Verma", "email": "asha@example.com", "phone": "9876500000", "city": "Pune",
           "pincode": "411001", "address": "12 MG Road"}



@pytest.mark.parametrize("name, want", [
    ("First Name", "first_name"), ("Full Name *", "full_name"), ("Name", "full_name"), ("Email address", "email"),
    ("Mobile number", "phone"), ("Date of Birth", "birth_date"), ("PIN code", "pincode"), ("Pincode", "pincode"),
    ("Company name", "company"), ("Name of the company", "company"), ("Father's name", None), ("User name", None),
    ("Password", "secret"), ("Confirm password", "secret"), ("OTP", "secret"), ("Enter verification code", "secret"),
    ("PIN", "secret"), ("Card number", "secret"), ("CVV", "secret"), ("Aadhaar number", "secret"), ("PAN", "secret"),
    ("Search", None),
])
def test_what_each_box_asks_for(name, want):
    assert forms.classify(name) == want


def box(name, password=False, filled=False):
    return {"name": name, "type": "Edit", "password": password, "filled": filled}


def test_plan_fills_known_boxes_and_leaves_secrets():
    boxes = [box("Full name"), box("Email"), box("Confirm email"), box("Password", password=True), box("OTP"),
             box("City", filled=True), box("Favourite colour"), box("First name")]
    to_fill, left, unknown = forms.plan(boxes, DETAILS)
    assert [k for _, k in to_fill] == ["full_name", "email", "first_name"]
    assert "Password" in left and "OTP" in left and "Confirm email" in left
    assert "Favourite colour" in unknown
    assert forms.value_for("first_name", DETAILS) == "Asha"            # worked out from the full name


def test_cards_and_ids_are_not_saved():
    saved, err = forms.save({"full_name": "4111 1111 1111 1111"})
    assert saved is None and err
    saved, err = forms.save({**DETAILS, "password": "hunter2"})          # not a field: dropped
    assert not err and "password" not in saved and forms.load()["email"] == "asha@example.com"


def test_fill_form_never_tells_the_brain_the_values(monkeypatch):
    forms.save(DETAILS)
    typed = []

    class Ctrl:
        def SetFocus(self):
            pass

    boxes = [{"name": n, "type": "Edit", "password": n == "Password", "filled": False, "offscreen": False,
              "x": 10, "y": 10 * i, "ctrl": Ctrl()} for i, n in enumerate(["Full name", "Email", "Password", "OTP"])]
    monkeypatch.setattr(tools.forms, "_form_boxes", lambda window: ((1, "Sign up - Chrome", "chrome.exe"), boxes))
    monkeypatch.setattr(tools.forms, "_nova_click", lambda *a, **k: None)
    monkeypatch.setattr(tools.forms, "_focused_is_password", lambda: False)
    monkeypatch.setattr(tools.keyboard, "write", lambda text, delay=0: typed.append(text))
    monkeypatch.setattr(tools.keyboard, "send", lambda keys: None)
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    out = tools.fill_form("Sign up")
    assert typed == ["Asha Verma", "asha@example.com"]                   # typed on the laptop...
    assert "Asha" not in out and "asha@" not in out                      # ...but never said to the brain
    assert "Password" in out and "OTP" in out and "NOT submit" in out


def test_fill_form_without_details_points_to_settings():
    assert "📝 Forms" in tools.fill_form()


def test_typing_into_a_password_box_is_refused(monkeypatch):
    monkeypatch.setattr(tools.forms, "_focused_is_password", lambda: True)
    written = []
    monkeypatch.setattr(tools.keyboard, "write", lambda *a, **k: written.append(a))
    assert tools.type_text("hunter2") == tools.NO_SECRETS and not written


def test_fill_in_refuses_a_password_box(monkeypatch):
    class Ctrl:
        IsPassword = True
    items = [{"name": "Password", "type": "Edit", "x": 1, "y": 1, "offscreen": False, "ctrl": Ctrl()}]
    monkeypatch.setattr(tools.forms, "_scan", lambda window, limit=0: ((1, "Login", "chrome.exe"), items))
    assert tools.fill_in("hunter2", box="Password") == tools.NO_SECRETS


def test_filling_a_form_asks_first_in_careful_mode():
    assert "fill_form" in tools.RISKY
    assert "help_login" not in tools.RISKY and "wait_for_code" not in tools.RISKY   # they only click into a box


def test_settings_page_saves_details(client):
    r = client.post("/api/forms", json={"details": DETAILS})
    assert r.status_code == 200
    got = {f["key"]: f["value"] for f in r.get_json()["fields"]}
    assert got["city"] == "Pune" and got["first_name"] == ""
    bad = client.post("/api/forms", json={"details": {"phone": "4111111111111111"}})
    assert bad.status_code == 400


def test_brain_is_told_never_to_ask_for_passwords():
    assert "NEVER ask the user for a" in app.PERSONA and "wait_for_code" in app.PERSONA
