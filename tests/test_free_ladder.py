"""The free ladder: the strongest free cloud model first, the next one when its limit is used up (nova/brain/usage.py)."""
import pytest

from nova import store
from nova.brain import cloud, usage

LADDER = ["gemini-4-hydrogen", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3-flash-preview", "gemini-2.5-flash",
          "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]
DAILY_429 = "Quota exceeded for metric: free_tier_requests per day, limit: 250. Please retry in 33s."
HI = [{"role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def google_key():
    cloud.set_key("google", "test-key")
    yield
    cloud.set_key("google", "")


def fake_google(monkeypatch, out=()):
    """Google that says 429 for the models in `out`; returns the models each request asked for."""
    asked = []

    def http(method, url, key, body=None, timeout=90):
        asked.append(body["model"])
        if body["model"] in out:
            raise cloud.CloudError("limit (429)", 429, DAILY_429)
        return {"choices": [{"message": {"content": "hi from " + body["model"]}}]}
    monkeypatch.setattr(cloud, "_http", http)
    return asked


def test_the_ladder_is_strongest_first_and_on_by_default():
    assert usage.ladder("google") == LADDER and usage.ladder("openai") == []
    assert usage.ladder_on()
    assert usage.route("google", "gemini-3.8-flash") == LADDER     # whichever free one is chosen, start at the top


def test_the_strongest_free_model_answers(monkeypatch):
    asked = fake_google(monkeypatch)
    _, text, _ = cloud.chat("cloud:google:gemini-3.8-flash", HI, [])
    assert asked == ["gemini-4-hydrogen"] and text == "hi from gemini-4-hydrogen"
    assert cloud.LAST["model"] == "gemini-4-hydrogen"


def test_a_used_up_model_hands_over_to_the_next_one_in_the_same_call(monkeypatch):
    asked = fake_google(monkeypatch, out={"gemini-4-hydrogen", "gemini-3.8-flash"})
    _, text, _ = cloud.chat("cloud:google:gemini-3.8-flash", HI, [])
    assert asked == ["gemini-4-hydrogen", "gemini-3.8-flash", "gemini-3.6-flash"]
    assert text == "hi from gemini-3.6-flash"
    log = store.current().task_log.read_text(encoding="utf-8")
    assert "FREE LADDER google:gemini-4-hydrogen used up -> trying gemini-3.8-flash" in log
    asked.clear()
    cloud.chat("cloud:google:gemini-3.8-flash", HI, [])
    assert asked == ["gemini-3.6-flash"]                  # the used-up ones aren't asked again today


def test_every_model_used_up_refuses_without_calling_google(monkeypatch):
    for name in LADDER:
        usage.hit_limit("google", name, DAILY_429)
    asked = fake_google(monkeypatch)
    with pytest.raises(cloud.CloudError, match="All my free Google models are used up"):
        cloud.chat("cloud:google:gemini-3.8-flash", HI, [])
    assert asked == []


def test_other_errors_do_not_climb_down_the_ladder(monkeypatch):
    asked = []

    def down(*a, **k):
        asked.append(1)
        raise cloud.CloudError("Gemini is overloaded (503)", 503)
    monkeypatch.setattr(cloud, "_http", down)
    with pytest.raises(cloud.CloudError, match="overloaded"):
        cloud.chat("cloud:google:gemini-3.8-flash", HI, [])
    assert len(asked) == 1


def test_ladder_off_or_a_model_off_the_ladder_uses_only_that_model(monkeypatch):
    asked = fake_google(monkeypatch)
    cloud.chat("cloud:google:gemini-4-argon", HI, [])       # paid, not a rung
    usage.set_ladder(False)
    cloud.chat("cloud:google:gemini-3.8-flash", HI, [])
    assert asked == ["gemini-4-argon", "gemini-3.8-flash"]


def test_a_thought_signature_goes_back_only_to_the_model_that_wrote_it(monkeypatch):
    sent = []

    def http(method, url, key, body=None, timeout=90):
        sent.append(body)
        return {"choices": [{"message": {"content": "ok"}}]}
    monkeypatch.setattr(cloud, "_http", http)
    sig = {"google": {"thought_signature": "real-one"}}
    history = HI + [{"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "look_up", "arguments": {}}, "extra_content": sig, "by": "gemini-4-hydrogen"}]},
        {"role": "tool", "tool_name": "look_up", "content": "x"}]
    usage.set_ladder(False)
    cloud.chat("cloud:google:gemini-4-hydrogen", history, [])
    cloud.chat("cloud:google:gemini-3.8-flash", history, [])
    got = [next(m for m in b["messages"] if m.get("tool_calls"))["tool_calls"][0]["extra_content"]["google"]
           ["thought_signature"] for b in sent]
    assert got == ["real-one", "skip_thought_signature_validator"]


def test_the_meter_lists_the_rungs_and_which_one_is_in_use():
    usage.hit_limit("google", "gemini-4-hydrogen", DAILY_429)
    snap = usage.snapshot("google", "gemini-3.8-flash")
    assert [r["model"] for r in snap["models"]][:len(LADDER)] == LADDER
    assert [r["rung"] for r in snap["models"][:len(LADDER)]] == list(range(1, len(LADDER) + 1))
    assert snap["models"][1]["left"] == 20               # config/cloud_free.json's limit (hydrogen learned 250 above)
    assert snap["ladder"] == {"on": True, "rungs": LADDER, "in_use": True, "current": "gemini-3.8-flash"}
    assert [r["model"] for r in snap["models"] if r["current"]] == ["gemini-3.8-flash"]


def test_ladder_api(client):
    from nova.core import app
    app.config.save_settings(dict(app.config.settings(), provider="google", cloud_models={"google": "gemini-3.8-flash"}))
    assert client.post("/api/cloud/ladder", json={"on": "yes"}).status_code == 400
    j = client.post("/api/cloud/ladder", json={"on": False}).get_json()
    assert j["ladder"]["on"] is False and j["ladder"]["in_use"] is False and not usage.ladder_on()
