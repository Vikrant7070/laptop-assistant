"""/api/model: which VRM file nova3d.html loads for the chosen character, and the colours it was made with."""
from nova.core import app


def test_each_character_gets_her_own_model(client, monkeypatch):
    for cid, vrm, hair in (("nova", "nova.vrm", "#6b4fe0"), ("makima", "makima.vrm", "#d9776a"),
                           ("lucy", "lucy.vrm", "#f7d774")):
        monkeypatch.setattr(app, "settings", lambda cid=cid: {"character": cid})
        j = client.get("/api/model").get_json()
        assert j["vrm"] == vrm
        assert j["base"]["hair"] == hair      # her character's own look: change_look tints from here


def test_missing_model_file_means_toon_body(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app, "settings", lambda: {"character": "lucy"})
    monkeypatch.setattr(app, "ROOT", tmp_path)          # no static/nova3d/lucy.vrm there
    assert client.get("/api/model").get_json()["vrm"] is None
