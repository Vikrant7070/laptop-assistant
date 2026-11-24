"""📝 Form profile (manager plan Phase 7): details saved encrypted (DPAPI; the fake one in tests), noticed when the user
types them into a form and asked about ONCE with a card that names no values, used by fill_form, never in a brain
message, and passwords / codes / card / bank / Aadhaar / PAN / passport numbers refused even when asked."""
import json

import pytest

from nova import store
from nova.actions import activity, forms, tools
from nova.core import app
from nova.core.app import asking, privacy

DETAILS = {"full_name": "Asha Verma", "email": "asha@example.com", "phone": "9876500000", "city": "Pune"}
SECRET_VALUES = ("Asha Verma", "asha@example.com", "9876500000", "asha.new@example.org", "9123400000")


@pytest.fixture(autouse=True)
def fresh():
    forms.notice.forget()
    privacy._later.update(keys=[], at=0.0)
    yield
    app.state.pop("question", None)


def test_the_profile_round_trips_encrypted():
    saved, err = forms.save(DETAILS)
    assert not err and forms.load()["email"] == "asha@example.com"
    raw = store.current().forms.read_text(encoding="utf-8")
    assert json.loads(raw)["v"] == 2
    assert not any(v in raw for v in DETAILS.values())                  # nothing readable on disk


def test_an_old_plain_file_is_encrypted_on_first_read():
    store.current().forms.write_text(json.dumps(DETAILS), encoding="utf-8")
    assert forms.load()["city"] == "Pune"
    assert "Pune" not in store.current().forms.read_text(encoding="utf-8")


def test_unreadable_on_another_account_gives_blanks_not_a_crash():
    store.current().forms.write_text(json.dumps({"v": 2, "enc": "bm90LWRwYXBp"}), encoding="utf-8")
    assert forms.load()["email"] == ""


@pytest.mark.parametrize("details", [
    {"full_name": "4111 1111 1111 1111"},             # a card number
    {"phone": "4111111111111111"},                     # a card number in the phone box
    {"address": "1234 5678 9012"},                     # Aadhaar
    {"company": "ABCDE1234F"},                         # PAN
    {"job_title": "K1234567"},                         # passport
    {"website": "HDFC0001234"},                        # IFSC
    {"passport": "K1234567"},                          # asked to keep a passport: refused, not dropped
    {"card_number": "4111111111111111"},
    {"aadhaar": "123412341234"},
    {"password": "hunter2"},
    {"otp": "482913"},
])
def test_secrets_are_refused_even_when_asked(details):
    saved, err = forms.save({**DETAILS, **details})
    assert saved is None and err
    assert forms.load()["email"] == ""                  # nothing was saved


def test_an_ordinary_phone_and_pin_code_are_fine():
    saved, err = forms.save({**DETAILS, "phone": "+91 98765 00000", "pincode": "411001"})
    assert not err and saved["pincode"] == "411001"


# ------------------------------------------------------------------ noticing what the user types

def _browser(desktop, monkeypatch, focus):
    """Chrome in front on a sign-up page; read_focus returns whatever focus["box"] is."""
    desktop.set_windows([(1, "Sign up - Shop - Google Chrome", "chrome.exe")], front=1)
    monkeypatch.setattr(activity, "read_url", lambda hwnd: "shop.example.com/signup")
    monkeypatch.setattr(activity, "read_focus", lambda: focus.get("box"))
    activity.save_prefs(on=True)


def _type_a_form(desktop, monkeypatch, boxes, offers):
    focus = {}
    _browser(desktop, monkeypatch, focus)
    s = activity.Sampler(user_here=lambda now: True, on_form_offer=offers.append)
    t = 1000.0
    for i, (name, value) in enumerate(boxes):
        focus["box"] = ((i,), name, value)
        s.tick(t)
        t += 3
    focus["box"] = None
    s.tick(t)                                            # clicked Submit: focus left the boxes...
    assert offers == []                                  # ...still on the form page: not yet
    desktop.set_windows([(1, "Thank you - Shop - Google Chrome", "chrome.exe")], front=1)
    s.tick(t + 3)                                        # the page moved on
    return s


def test_new_details_typed_into_a_form_are_offered_once_without_values(desktop, monkeypatch):
    forms.save(DETAILS)
    offers = []
    _type_a_form(desktop, monkeypatch, [("Email address", "asha.new@example.org"), ("Password", "hunter2"),
                                        ("Mobile number", "9123400000"), ("Full name", "Asha Verma")], offers)
    assert offers == [["email", "phone"]]                # the name is already saved; the password box is never kept
    out = privacy._offer_forms(offers[0])
    card = out["question"]
    text = json.dumps(card) + out["reply"]
    assert "email" in out["reply"] and "phone" in out["reply"]
    assert not any(v in text for v in SECRET_VALUES) and "hunter2" not in text
    asking.tap(card["id"], {"save": "yes"})
    assert forms.load()["email"] == "asha.new@example.org" and forms.load()["phone"] == "9123400000"
    # the same values typed again: never asked again
    offers.clear()
    forms.save(DETAILS)
    _type_a_form(desktop, monkeypatch, [("Email address", "asha.new@example.org")], offers)
    assert offers == []


def test_no_forgets_and_does_not_ask_again(desktop, monkeypatch):
    offers = []
    _type_a_form(desktop, monkeypatch, [("Email", "asha.new@example.org")], offers)
    out = privacy._offer_forms(offers[0])
    asking.tap(out["question"]["id"], {"save": "no"})
    assert forms.load()["email"] == ""
    offers.clear()
    _type_a_form(desktop, monkeypatch, [("Email", "asha.new@example.org")], offers)
    assert offers == []


def test_nothing_is_noticed_while_the_log_is_off_or_paused_or_switched_off(desktop, monkeypatch):
    offers = []
    focus = {"box": ((1,), "Email", "asha.new@example.org")}
    _browser(desktop, monkeypatch, focus)
    for change in ({"paused": True}, {"paused": False, "notice_forms": False}, {"on": False}):
        activity.save_prefs(**change)
        s = activity.Sampler(user_here=lambda now: True, on_form_offer=offers.append)
        s.tick(1000)
        focus["box"] = None
        desktop.set_windows([(1, "Done - Google Chrome", "chrome.exe")], front=1)
        s.tick(1003)
        assert offers == [] and forms.notice.pending == {}
        focus["box"] = ((1,), "Email", "asha.new@example.org")
        desktop.set_windows([(1, "Sign up - Shop - Google Chrome", "chrome.exe")], front=1)


def test_secret_or_odd_values_are_never_kept_in_memory():
    assert not forms.notice.wanted("Password") and not forms.notice.wanted("OTP") and not forms.notice.wanted("Card number")
    assert not forms.notice.seen("Mobile number", "4111 1111 1111 1111")
    assert not forms.notice.seen("Email", "not an email")
    assert not forms.notice.seen("Company", "ABCDE1234F")
    assert forms.notice.pending == {}


def test_the_profile_never_reaches_the_brain(desktop, monkeypatch):
    """Noticed, saved after Yes, then "fill in this form": the brain's messages never hold a value."""
    offers = []
    _type_a_form(desktop, monkeypatch, [("Email address", "asha.new@example.org"), ("Full name", "Asha Verma"),
                                        ("Mobile number", "9123400000")], offers)
    out = privacy._offer_forms(offers[0])
    asking.tap(out["question"]["id"], {"save": "yes"})

    class Ctrl:
        def SetFocus(self):
            pass

    boxes = [{"name": n, "type": "Edit", "password": False, "filled": False, "offscreen": False, "x": 10,
              "y": 10 * i, "ctrl": Ctrl()} for i, n in enumerate(["Full name", "Email", "Phone"])]
    monkeypatch.setattr(tools.forms, "_form_boxes", lambda window: ((1, "Apply - Chrome", "chrome.exe"), boxes))
    monkeypatch.setattr(tools.forms, "_nova_click", lambda *a, **k: None)
    monkeypatch.setattr(tools.forms, "_focused_is_password", lambda: False)
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    fake = _Brain([("", [("fill_form", {"window": "Apply"})]), ("Filled it in - please check it.", [])])
    monkeypatch.setattr(app.llm, "chat", fake)
    monkeypatch.setattr(app.llm, "supports_tools", lambda m: True)
    monkeypatch.setattr(app.judging, "settle", lambda *a, **k: "")
    out, _ = app.answer("fill in this form")
    assert out["pending"][0]["tool"] == "fill_form"            # level 2: always asks Allow first
    app.confirm_pending(True)
    assert desktop.logged("write") == ["Asha Verma", "asha.new@example.org", "9123400000"]   # typed locally
    seen = json.dumps(fake.seen) + json.dumps(app.state["messages"])
    assert fake.seen and not any(v in seen for v in SECRET_VALUES)


class _Brain:
    """A scripted brain that keeps every message it was sent (like conftest's FakeBrain; importing conftest again
    would re-run its module-level patches)."""

    def __init__(self, script):
        self.script, self.seen = list(script), []

    def __call__(self, model, messages, tools_, think=False, on_text=None, fallback=None, on_think=None, **kw):
        self.seen.append(json.loads(json.dumps(messages, default=str)))
        text, calls = self.script.pop(0) if self.script else ("Done!", [])
        calls = [{"id": f"c{i}", "name": n, "args": a} for i, (n, a) in enumerate(calls)]
        stored = {"role": "assistant", "content": text}
        if calls:
            stored["tool_calls"] = [{"function": {"name": c["name"], "arguments": c["args"]}} for c in calls]
        if on_text and text:
            on_text(text)
        app.llm.LAST["fallback"] = None
        return stored, text, calls

