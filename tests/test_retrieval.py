"""Per-request picking: which tools the local brain sees, which facts and lessons go into her prompt."""
import pytest

from nova.core import app
from nova.actions import knowledge
from nova.brain import retrieval
from nova.brain import toolpick
from nova.actions import tools


@pytest.fixture
def words_only(monkeypatch):
    """Tests run on the word-overlap fallback, unless a test loads the real model itself."""
    monkeypatch.setattr(retrieval, "ready", lambda: False)


def names(schemas):
    return [t["name"] for t in schemas]


def test_pick_always_has_core_and_matches_request(words_only):
    picked = toolpick.pick("turn the volume up a bit")
    assert set(toolpick.CORE) <= set(picked)
    assert "set_volume" in picked
    assert len(picked) < len(tools.TOOLS) / 2
    assert picked == [t["name"] for t in tools.TOOLS if t["name"] in picked]     # fixed order: same set, same prompt


def test_pick_brings_the_whole_group(words_only):
    picked = toolpick.pick("search youtube for lofi and play the first video")
    assert {"fill_in", "click_nth", "list_clickable", "video_control"} <= set(picked)


def test_core_and_groups_name_real_tools():
    real = {t["name"] for t in tools.TOOLS}
    assert set(toolpick.CORE) <= real
    assert set().union(*toolpick.GROUPS) <= real
    assert set(toolpick.HINTS) <= real


def recording_brain(brain, script):
    fake, ran = brain(script)
    seen_tools = []

    def call(model, messages, tools_, **kw):
        seen_tools.append(names(tools_))
        return fake(model, messages, tools_, **kw)
    app.llm.chat = call          # (the brain fixture's monkeypatch restores llm.chat afterwards)
    return seen_tools, ran


def test_local_brain_sees_picked_tools_for_the_whole_task(brain, words_only):
    seen, ran = recording_brain(brain, [("", [("set_volume", {"action": "up"})]), ("Louder now!", [])])
    out, status = app.answer("make the volume louder")
    assert status == 200 and ran == [("set_volume", {"action": "up"})]
    assert len(seen) == 2 and seen[0] == seen[1]                 # unchanged between steps: Ollama's cache stays valid
    assert "set_volume" in seen[0] and "empty_recycle_bin" not in seen[0]


def test_more_tools_adds_what_she_asks_for(brain, words_only):
    seen, ran = recording_brain(brain, [("", [("more_tools", {"need": "screen brightness"})]),
                                        ("", [("set_brightness", {"percent": 40})]), ("Dimmed it.", [])])
    out, _ = app.answer("it's too bright in here, can you do something")
    assert "set_brightness" not in seen[0]
    assert "set_brightness" in seen[1] and seen[1] == seen[2]
    assert ran == [("set_brightness", {"percent": 40})]          # more_tools itself never reaches tools.run_tool
    assert "Found more tools" in out["activity"]


def test_tools_used_last_request_stay_for_the_next(brain, words_only):
    seen, _ = recording_brain(brain, [("", [("set_brightness", {"percent": 40})]), ("Done.", []), ("Okay.", [])])
    app.answer("set brightness to 40")
    app.answer("a bit more")
    assert "set_brightness" in seen[-1]


def test_cloud_brain_sees_every_tool(monkeypatch):
    monkeypatch.setattr(app.cloud, "parse", lambda m: ("gemini", "x") if m == "cloud" else None)
    app.state["task"] = {"tools": ["get_datetime"], "facts": ""}
    assert len(app.active_tools("cloud")) == len(tools.TOOLS) - 2          # all but more_tools and escalate
    assert names(app.active_tools("local")) == ["get_datetime"]


def test_picking_failure_falls_back_to_all_tools(brain, monkeypatch):
    monkeypatch.setattr(toolpick, "pick", lambda *a, **k: 1 / 0)
    seen, _ = recording_brain(brain, [("Hi!", [])])
    out, status = app.answer("hello")
    assert status == 200 and len(seen[0]) == len(tools.TOOLS)


def test_prompt_block_keeps_relevant_facts_and_lessons(words_only):
    knowledge.update_profile("name", "Vikrant")
    knowledge.update_profile("preferences", "Likes short spoken answers")
    for i in range(12):
        knowledge.update_profile("facts", f"Collects stamp number {i} from country {i}")
    knowledge.update_profile("facts", "Watches cricket matches on Hotstar every weekend")
    knowledge.update_profile("interests", "Plays guitar")
    for i in range(10):
        knowledge.learn_lesson(f"Organizing folder {i}", f"Preview organizing folder {i} before moving files", "mistake")
    knowledge.learn_lesson("Playing cricket highlights", "Hotstar needs a login check before playing cricket", "mistake")
    block = knowledge.prompt_block("put the cricket match on hotstar")
    assert "Vikrant" in block and "Likes short spoken answers" in block           # always there
    assert "Hotstar every weekend" in block and "login check" in block            # about this request
    assert block.count("stamp number") <= 4                                       # unrelated facts don't crowd it


def test_prompt_block_without_query_still_has_background(words_only):
    knowledge.update_profile("interests", "Plays guitar")
    knowledge.learn_lesson("Opening apps", "Wait for the window before typing", "mistake")
    block = knowledge.prompt_block()
    assert "Plays guitar" in block and "Wait for the window" in block


def test_bank_grows_past_old_limits(words_only):
    words = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike".split()
    for i in range(45):
        knowledge.update_profile("facts", f"{words[i % 13]}{i} {words[(i * 5) % 13]}x{i} {words[(i * 7) % 13]}y{i}")
    assert len(knowledge.facts()) == 45


@pytest.mark.parametrize("text,promise", [
    ("Arey, boss, locking your PC now - safe!", True), ("Okay, I'll do it right away.", True),
    ("Locking the screen now...", True), ("It is 3 PM right now.", False), ("Your battery is 80% and charging.", False)])
def test_promise_without_tool_is_caught(text, promise):
    """Seen live: for 'lock my pc' the small model says it's locking, calls no tool, and nothing happens."""
    assert bool(app.PROMISE_RE.search(text)) == promise


def test_recall_finds_by_meaning_with_model():
    if not (retrieval.CACHE / retrieval.REPO.replace("/", "--") / retrieval.FILES[0]).exists():
        pytest.skip("embedding model not downloaded")
    assert retrieval.load()
    knowledge.update_profile("interests", "Loves watching films on Netflix")
    knowledge.update_profile("interests", "Plays guitar")
    out = knowledge.recall("movies")                                   # no shared word with "films"
    assert "Netflix" in out and "guitar" not in out
    assert "set_volume" in toolpick.pick("make it quieter")
    assert "look_through_camera" in toolpick.pick("how do I look today?")
