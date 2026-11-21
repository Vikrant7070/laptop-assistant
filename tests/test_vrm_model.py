"""/api/model: which VRM nova3d.html loads for the chosen character, her own colours and how to fit moves on her."""
from nova.core import app, characters


def test_each_character_gets_her_own_model(client, monkeypatch):
    for cid, hair in (("nova", "#6b4fe0"), ("makima", "#d64b3c"), ("lucy", "#f7d774")):
        monkeypatch.setattr(app.config, "settings", lambda cid=cid: {"character": cid})
        j = client.get("/api/model").get_json()
        assert j["id"] == cid and j["vrm"].startswith(f"/characters/{cid}/model.vrm?v=")
        assert j["base"]["hair"] == hair                  # her character's own look: change_look tints from here
        assert j["fit"]["work"]["desk"] > 0               # her own body's fit (character.json)


def test_another_character_can_be_asked_for(client, monkeypatch):
    monkeypatch.setattr(app.config, "settings", lambda: {"character": "nova"})
    assert client.get("/api/model?character=lucy").get_json()["id"] == "lucy"


def test_model_file_is_served_from_her_folder(client):
    r = client.get("/characters/lucy/model.vrm")
    # The file itself on a laptop; a Git LFS pointer in CI, which checks out without LFS (roadmap R3).
    assert r.status_code == 200 and (r.data[:4] == b"glTF" or r.data.startswith(b"version https://git-lfs"))
    assert client.get("/characters/nobody/model.vrm").status_code == 404


def test_missing_model_file_means_no_model(client, monkeypatch):
    monkeypatch.setattr(app.config, "settings", lambda: {"character": "lucy"})
    monkeypatch.setattr(characters, "model_path", lambda ch: None)
    assert client.get("/api/model").get_json()["vrm"] is None


def test_new_export_gets_a_new_url(client, monkeypatch, tmp_path):
    """Re-exporting model.vrm from VRoid Studio changes the URL, so the live 3D page loads the new model."""
    import os
    f = tmp_path / "model.vrm"
    f.write_bytes(b"glTF")
    monkeypatch.setattr(app.config, "settings", lambda: {"character": "lucy"})
    monkeypatch.setattr(characters, "model_path", lambda ch: f)
    before = client.get("/api/model").get_json()["vrm"]
    os.utime(f, ns=(f.stat().st_mtime_ns + 10**9,) * 2)
    assert client.get("/api/model").get_json()["vrm"] != before
