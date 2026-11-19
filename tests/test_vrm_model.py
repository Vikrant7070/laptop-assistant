"""/api/model: which VRM nova3d.html loads for the chosen character, her own colours and how to fit moves on her."""
from nova.core import app, characters


def test_each_character_gets_her_own_model(client, monkeypatch):
    for cid, hair in (("nova", "#6b4fe0"), ("makima", "#d64b3c"), ("lucy", "#f7d774")):
        monkeypatch.setattr(app, "settings", lambda cid=cid: {"character": cid})
        j = client.get("/api/model").get_json()
        assert j["id"] == cid and j["vrm"] == f"/characters/{cid}/model.vrm"
        assert j["base"]["hair"] == hair                  # her character's own look: change_look tints from here
        assert j["fit"]["work"]["desk"] > 0               # her own body's fit (character.json)


def test_another_character_can_be_asked_for(client, monkeypatch):
    monkeypatch.setattr(app, "settings", lambda: {"character": "nova"})
    assert client.get("/api/model?character=lucy").get_json()["id"] == "lucy"


def test_model_file_is_served_from_her_folder(client):
    r = client.get("/characters/lucy/model.vrm")
    assert r.status_code == 200 and r.data[:4] == b"glTF"
    assert client.get("/characters/nobody/model.vrm").status_code == 404


def test_missing_model_file_means_toon_body(client, monkeypatch):
    monkeypatch.setattr(app, "settings", lambda: {"character": "lucy"})
    monkeypatch.setattr(characters, "model_path", lambda ch: None)
    assert client.get("/api/model").get_json()["vrm"] is None
