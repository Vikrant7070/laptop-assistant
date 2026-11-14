"""Skills: text-file routines and recipes (skills.py) and how the conversation loop runs them."""
import json
import shutil
from pathlib import Path

import app
import skills
import tools

EXAMPLES = Path(__file__).resolve().parent.parent / "skills"

ROUTINE = """---
name: movie-time
description: Get ready for a movie
when: movie time, let's watch a movie
---
- minimize_all_windows
- open_website url_or_search=netflix.com
- set_volume action=up, steps=10
- say Enjoy the movie!
"""


def set_autonomy(mode):
    s = app.settings()
    s["autonomy"] = mode
    app.SETTINGS_FILE.write_text(json.dumps(s), encoding="utf-8")


def write(name, text):
    skills.DIR.mkdir(parents=True, exist_ok=True)
    (skills.DIR / f"{name}.md").write_text(text, encoding="utf-8")


# ------------------------------------------------------------------ parsing

def test_routine_steps_parse():
    s = skills.parse(ROUTINE)
    assert s["kind"] == "routine" and s["name"] == "movie-time"
    assert s["steps"][1] == {"tool": "open_website", "args": {"url_or_search": "netflix.com"}}
    assert s["steps"][2] == {"tool": "set_volume", "args": {"action": "up", "steps": 10}}
    assert s["steps"][3] == {"say": "Enjoy the movie!"}
    assert s["tools"] == ["minimize_all_windows", "open_website", "set_volume"]


def test_step_forms():
    assert skills.parse_step('open_app name="Visual Studio Code"') == {"tool": "open_app", "args": {"name": "Visual Studio Code"}}
    assert skills.parse_step('set_dark_mode {"enabled": true}') == {"tool": "set_dark_mode", "args": {"enabled": True}}
    assert skills.parse_step("set_dark_mode enabled=false") == {"tool": "set_dark_mode", "args": {"enabled": False}}
    assert skills.parse_step("wait 2") == {"wait": 2.0}
    assert skills.parse_step("open Chrome and play music") is None
    assert skills.parse_step("format_drive drive=C") is None           # not a tool
    assert skills.parse_step("run_skill name=x") is None                # no skills inside skills


def test_plain_english_is_a_recipe():
    s = skills.parse("---\nname: report\n---\n1. Look at my notes with list_notes\n2. Write a short summary")
    assert s["kind"] == "recipe" and s["tools"] == ["list_notes"]


def test_routine_with_a_bad_line_is_broken():
    s = skills.parse("---\nname: x\nkind: routine\n---\n- open_app name=Spotify\n- dance wildly")
    assert s["kind"] == "broken" and "dance wildly" in s["error"]


def test_shipped_examples_all_parse():
    for f in EXAMPLES.glob("*.md"):
        s = skills.parse(f.read_text(encoding="utf-8"), f.stem)
        assert s and s["kind"] in ("routine", "recipe"), f"{f.name}: {s and s['error']}"


def test_input_is_filled_and_url_safe():
    args = skills.fill({"url_or_search": "https://youtube.com/results?search_query={input_url}", "t": "{input}"}, "lo fi & chill")
    assert args == {"url_or_search": "https://youtube.com/results?search_query=lo+fi+%26+chill", "t": "lo fi & chill"}


# ------------------------------------------------------------------ the folder

def test_match_by_trigger_phrase_and_name():
    write("movie-time", ROUTINE)
    assert skills.match("hey nova, movie time!")["name"] == "movie-time"
    assert skills.match("Let's watch a movie")["name"] == "movie-time"
    assert skills.match("what's the battery level") is None


def test_save_list_and_delete():
    msg = skills.save("Movie Time", "Get ready for a movie", "minimize_all_windows\nopen_website url_or_search=netflix.com",
                      when="movie time")
    assert "Saved the routine skill 'movie-time' with 2 steps" in msg
    assert "Updated" in skills.save("movie-time", "Movie", "- minimize_all_windows")
    assert "movie-time (routine)" in skills.describe()
    assert "Deleted" in skills.delete("movie-time")
    assert skills.get("movie-time") is None
    assert list((skills.DIR / ".trash").glob("movie-time-*.md"))      # not gone for good


def test_save_recipe_and_reject_bad_routine_and_secrets():
    assert "recipe" in skills.save("report", "weekly report", "Read my notes\nWrite a summary of the week")
    assert "isn't a step" in skills.save("x", "x", "open_app name=Spotify\nthen dance", kind="routine")
    assert "won't save" in skills.save("login", "log in", "type_text text=my password is hunter2")


def test_skill_tools_registered():
    for n in ("run_skill", "save_skill", "list_skills", "delete_skill"):
        assert n in tools.FUNCS


# ------------------------------------------------------------------ in the conversation

def test_routine_runs_every_step_without_the_brain(brain):
    set_autonomy("trusted")
    write("movie-time", ROUTINE)
    fake, ran = brain([("", [("run_skill", {"name": "movie-time"})]), ("Enjoy the movie!", [])])
    out, _ = app.answer("movie time")
    assert [n for n, _ in ran] == ["minimize_all_windows", "open_website", "set_volume"]
    assert ran[2][1] == {"action": "up", "steps": 10}
    assert len(fake.seen) == 2                          # one brain call to start it, one to answer
    result = fake.seen[1][-1]["content"]
    assert "Ran the skill 'movie-time'" in result and "Enjoy the movie!" in result


def test_matching_skill_is_in_the_prompt(brain):
    write("movie-time", ROUTINE)
    fake, ran = brain([("Okay!", [])])
    app.answer("let's watch a movie")
    system = fake.seen[0][0]["content"]
    assert "run_skill(name='movie-time')" in system
    assert "run_skill" in app.state["task"]["tools"]


def test_routine_with_risky_step_asks_first(brain):
    set_autonomy("careful")
    write("close-chrome", "---\nname: close-chrome\n---\n- kill_process name_or_pid=chrome\n- say Closed.")
    fake, ran = brain([("", [("run_skill", {"name": "close-chrome"})]), ("Closed.", [])])
    out, _ = app.answer("close chrome please")
    assert out["pending"][0]["tool"] == "run_skill" and not ran
    app.confirm_pending(True)
    assert ran == [("kill_process", {"name_or_pid": "chrome"})]


def test_routine_needing_input_asks_for_it(brain):
    shutil.copy(EXAMPLES / "youtube-search.md", skills.DIR / "youtube-search.md") if skills.DIR.exists() else (
        skills.DIR.mkdir(parents=True), shutil.copy(EXAMPLES / "youtube-search.md", skills.DIR / "youtube-search.md"))
    fake, ran = brain([("", [("run_skill", {"name": "youtube-search"})]),
                       ("", [("run_skill", {"name": "youtube-search", "input": "lofi beats"})]), ("Here you go", [])])
    app.answer("search youtube for lofi beats")
    assert "input='lofi beats'" in fake.seen[0][0]["content"]
    assert ran == [("open_website", {"url_or_search": "https://www.youtube.com/results?search_query=lofi+beats"})]


def test_recipe_hands_instructions_to_the_brain(brain):
    write("report", "---\nname: report\nwhen: weekly report\nkind: recipe\n---\n1. Call list_notes\n2. Summarise them")
    fake, ran = brain([("On it", [("list_notes", {})]), ("Here's your week.", [])])
    app.answer("do my weekly report")
    assert "Follow it, one tool call at a time" in fake.seen[0][0]["content"]
    assert "list_notes" in app.state["task"]["tools"]
