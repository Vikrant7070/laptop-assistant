"""Nova as a co-worker: background email drafts and documents, then "want to see it?" -> "yes" brings it forward."""
import time
import urllib.parse

import pytest

from nova.core import app
from nova.actions import coworker
from nova.actions import tools


def wait_for(cond, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def fake_desktop(monkeypatch):
    """A pretend browser/Notepad: windows open 'hidden', titles load instantly, nothing real appears."""
    opened = []
    monkeypatch.setattr(coworker, "open_hidden", lambda args, proc, match=None, wait=15: (opened.append((args, proc)), 4242)[1])
    monkeypatch.setattr(coworker, "_browser", lambda: ("chrome.exe", "chrome.exe"))
    monkeypatch.setattr(coworker, "_title", lambda h: "Compose Mail - me@example.com - Gmail - Google Chrome")
    monkeypatch.setattr(coworker._user32, "IsWindow", lambda h: True)
    monkeypatch.setattr(coworker, "keep_hidden", lambda h, front=None: None)
    real_sleep = time.sleep
    monkeypatch.setattr(coworker.time, "sleep", lambda s: real_sleep(0.001))
    return opened


def writer(text):
    return lambda prompt, n: text


def test_compose_link_is_encoded_and_skips_names():
    url = coworker.gmail_compose_url("", "Hi & bye", "Line 1\nLine 2 ?=&")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert url.startswith("https://mail.google.com/mail/?")
    assert q["su"] == ["Hi & bye"] and q["body"] == ["Line 1\nLine 2 ?=&"] and "to" not in q
    assert q["view"] == ["cm"]


def test_parse_email_reply():
    assert coworker._parse_email("Subject: Leave on Friday\n\nHi Sir,\nI need leave.", "") == \
        ("Leave on Friday", "Hi Sir,\nI need leave.")
    assert coworker._parse_email("Hello there", "Given") == ("Given", "Hello there")


def test_email_drafted_in_background_then_offered(fake_desktop, monkeypatch):
    monkeypatch.setattr(app, "write_text", lambda p, n=700: "Subject: Project update\n\nHi Priya,\nAll on track.\nThanks")
    coworker.configure(write=lambda p, n: app.write_text(p, n), on_done=app.job_done, on_note=lambda t: None)
    out = tools.run_tool("draft_email", {"about": "tell Priya the project is on track", "to": "priya@example.com"})
    assert "background" in out
    job = coworker.jobs[-1]
    assert wait_for(lambda: job["status"] != "working")
    assert job["status"] == "done", job["error"]
    args, proc = fake_desktop[0]
    assert proc == "chrome.exe" and "--new-window" in args and "to=priya%40example.com" in args[-1]
    assert "Project update" in job["result"] and "Not sent" in job["result"]
    # she says it's ready and offers to show it
    assert wait_for(lambda: app.offer["job"] is job)
    ev = [e for e in app.events if e.get("kind") == "job"][-1]
    assert ev["offer"]["id"] == job["id"] and "Want to see it?" in ev["reply"]
    assert wait_for(lambda: any("Want to see it?" in s for s in app.mouth.said))   # spoken just after the offer


def test_yes_brings_the_window_forward(fake_desktop, monkeypatch, client):
    shown = []
    monkeypatch.setattr(coworker, "show", lambda job: (shown.append(job), "Here it is.")[1])
    job = {"id": 9, "kind": "draft_email", "title": "Hi", "status": "done", "hwnd": 1, "result": "ok", "error": ""}
    app.offer.update(job=job, until=time.time() + 60)
    j = client.post("/api/chat", json={"text": "yes please"}).get_json()
    assert j["reply"] == "Here it is." and shown == [job] and app.offer["job"] is None


def test_yes_by_voice_and_no(monkeypatch):
    shown = []
    monkeypatch.setattr(coworker, "show", lambda job: (shown.append(job), "Here it is.")[1])
    job = {"id": 3, "kind": "draft_email", "title": "Hi", "status": "done", "hwnd": 1}
    app.offer.update(job=job, until=time.time() + 60)
    app.heard("Yeah, show me.", via="silent")
    assert shown == [job]
    app.offer.update(job=job, until=time.time() + 60)
    assert app.take_offer("no thanks") .startswith("Okay")
    assert app.offer["job"] is None and len(shown) == 1


def test_a_real_request_is_not_eaten_by_the_offer(brain):
    app.offer.update(job={"id": 1, "title": "x"}, until=time.time() + 60)
    brain([("It's sunny!", [])])
    out, _ = app.answer("what's the weather like in Delhi today, can you check it for me")
    assert out["reply"] == "It's sunny!" and app.offer["job"] is not None


def test_offer_expires(monkeypatch):
    app.offer.update(job={"id": 1, "title": "x"}, until=time.time() - 1)
    assert app.take_offer("yes") is None


def test_offer_buttons(client, monkeypatch):
    monkeypatch.setattr(coworker, "show", lambda job: "Here it is.")
    app.offer.update(job={"id": 1, "title": "x"}, until=time.time() + 60)
    assert client.post("/api/offer", json={"show": True}).get_json()["reply"] == "Here it is."
    assert "isn't waiting" in client.post("/api/offer", json={"show": True}).get_json()["reply"]


def test_name_instead_of_address_leaves_to_empty(fake_desktop):
    coworker.configure(write=writer("Subject: Hello\n\nHi Mom, see you soon."), on_done=None)
    job, _ = coworker.draft_email(to="Mom", about="say hi")
    assert wait_for(lambda: job["status"] != "working")
    assert "to=" not in fake_desktop[0][0][-1]
    assert "don't have Mom's address" in job["result"]


def test_gmail_sign_in_page_puts_text_on_clipboard(fake_desktop, monkeypatch):
    clip = []
    monkeypatch.setattr(coworker, "_title", lambda h: "Gmail - Sign in - Google Accounts")
    monkeypatch.setattr(tools, "set_clipboard", lambda t: clip.append(t))
    coworker.configure(write=writer("Subject: Hello\n\nHi there, this is the body."), on_done=None)
    job, _ = coworker.draft_email(about="say hi")
    assert wait_for(lambda: job["status"] != "working")
    assert job["status"] == "done" and job.get("needs_login") and "sign in" in job["result"]
    assert clip and clip[0].startswith("Subject: Hello")


def test_failed_job_is_reported(fake_desktop, monkeypatch):
    coworker.configure(write=writer(""), on_done=app.job_done)
    job, _ = coworker.draft_email(about="anything")
    assert wait_for(lambda: job["status"] == "failed")
    assert wait_for(lambda: any("didn't work" in s for s in app.mouth.said))
    assert app.offer["job"] is None


def test_document_saved_and_opened_hidden(fake_desktop, monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "HOME", tmp_path)
    coworker.configure(write=writer("1. Milk\n2. Bread\n3. Eggs and a few more things"), on_done=None)
    job, _ = coworker.write_document(title="Shopping: list?", about="a shopping list")
    assert wait_for(lambda: job["status"] != "working")
    assert job["status"] == "done", job["error"]
    path = tmp_path / "Documents" / "Nova" / "Shopping list.txt"
    assert path.read_text(encoding="utf-8").startswith("1. Milk")
    assert fake_desktop[0][1] == "notepad.exe"
    job2, _ = coworker.write_document(title="Shopping: list?", about="again")
    assert wait_for(lambda: job2["status"] != "working")
    assert (tmp_path / "Documents" / "Nova" / "Shopping list (2).txt").exists()


def test_nova_does_not_nap_while_a_job_runs(fake_desktop, monkeypatch):
    import threading
    gate = threading.Event()
    coworker.configure(write=lambda p, n: (gate.wait(5), "Subject: x\n\nlong enough body")[1], on_done=None)
    job, _ = coworker.draft_email(about="x")
    app.nap["last_task"] = time.time() - app.IDLE_NAP - 5
    assert not app.nap_due()
    gate.set()
    assert wait_for(lambda: job["status"] != "working")
    assert app.nap_due()


def test_tools_are_picked_for_email_requests():
    from nova.brain import toolpick
    assert "draft_email" in toolpick.pick("draft an email to my manager asking for leave on friday")
    assert "read_notifications" in toolpick.pick("read my notifications")
    assert "show_finished_work" in toolpick.pick("show me the email draft")
