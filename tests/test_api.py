"""HTTP API: request validation and protection against other websites driving Nova (CSRF / DNS rebinding)."""
import pytest

from nova.core import app


def test_status_ok(client, monkeypatch):
    monkeypatch.setattr(app.llm, "list_models", lambda: None)
    r = client.get("/api/status")
    assert r.status_code == 200 and r.json["name"] == "Nova"


@pytest.mark.parametrize("host", ["evil.example.com", "attacker.test:5055", "127.0.0.1.nip.io:5055"])
def test_foreign_host_header_rejected(client, host):
    """DNS rebinding: a website whose name resolves to 127.0.0.1 must not reach the API."""
    r = client.post("/api/chat", json={"text": "hi"}, headers={"Host": host})
    assert r.status_code == 403


@pytest.mark.parametrize("path", ["/api/voice_typing", "/api/reset", "/api/show_window", "/api/setup"])
def test_cross_site_post_rejected(client, path):
    r = client.post(path, json={}, headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_same_origin_post_allowed(client, brain):
    brain([("Hi!", [])])
    r = client.post("/api/chat", json={"text": "hi"}, headers={"Origin": "http://127.0.0.1:5055"})
    assert r.status_code == 200


def test_chat_rejects_empty_and_non_string(client):
    assert client.post("/api/chat", json={"text": ""}).status_code == 400
    assert client.post("/api/chat", json={"text": 123}).status_code == 400
    assert client.post("/api/chat", json=["x"]).status_code == 400


def test_setup_rejects_bad_types(client, monkeypatch):
    monkeypatch.setattr(app.llm, "list_models", lambda: [])
    assert client.post("/api/setup", json={"model": 5}).status_code == 400
    assert client.post("/api/setup", json={"cloud_model": ["x"], "provider": "google"}).status_code == 400


def test_setup_ignores_unknown_autonomy(client, monkeypatch):
    monkeypatch.setattr(app.llm, "list_models", lambda: [])
    client.post("/api/setup", json={"autonomy": "god-mode"})
    assert app.settings()["autonomy"] == "careful"


def test_setup_write_is_atomic(client, monkeypatch):
    monkeypatch.setattr(app.llm, "list_models", lambda: [])
    client.post("/api/setup", json={"autonomy": "trusted"})
    assert not list(app.SETTINGS_FILE.parent.glob("*.tmp"))
    assert app.settings()["autonomy"] == "trusted"


def test_knowledge_edit_validation(client):
    assert client.post("/api/knowledge", json={"do": "lesson_delete", "id": "abc"}).status_code == 400
    assert client.post("/api/knowledge", json={"do": "nope"}).status_code == 400
    r = client.post("/api/knowledge", json={"do": "profile_add", "field": "city", "value": "Pune"})
    assert r.status_code == 200 and client.get("/api/knowledge").json["profile"]["city"] == "Pune"


def test_cloud_key_unknown_provider(client):
    assert client.post("/api/cloud/key", json={"provider": "x", "key": "k"}).status_code == 400


def test_events_queue_bounded():
    for i in range(1000):
        app.push_event({"me": str(i)})
    assert len(app.events) <= app.MAX_EVENTS


def test_host_allowed():
    from nova.shared import host_allowed
    assert host_allowed("127.0.0.1:5055", 5055) and host_allowed("localhost:5056", 5056)
    assert not host_allowed("127.0.0.1:5055", 5056)
    assert not host_allowed("evil.com:5055", 5055) and not host_allowed("", 5055) and not host_allowed(None, 5055)
