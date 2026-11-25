"""Manager plan 4f - remember and learn: one record per task, app notes, app paths as skills, lessons from
corrections (used only once the user ticks them)."""
import time

from nova import db, task_state
from nova.actions import appnotes, knowledge, learner, lessons, questions
from nova.core import app
from nova.core.app import learning

REAL_REVIEW = learning.review_turn          # conftest replaces it in every test; one test here runs the real one


def _step(tool, ok=True, out="ok", **args):
    return {"tool": tool, "args": args, "ok": ok, "out": out, "t": time.time()}


def _ep(goal, trace, tid, verdict=True, app_name=""):
    task = {"id": tid, "started": time.time() - 30, "goal": goal, "trace": trace, "app": app_name,
            "verdict": {"verdict": verdict, "source": "checks", "results": [{"check": "x", "ok": verdict}]}}
    return learner.episode_from_task(task)


# ------------------------------------------------------------------ one record per task

def test_the_episode_lives_on_the_task_cards_row():
    card = task_state.new("open notepad and type hi", tid="t-1")
    db.save_task(card)
    learner.record(_ep("open notepad and type hi", [_step("open_app", name="notepad"), _step("type_text", text="hi")], "t-1"))
    assert db.task_episode("t-1")["goal"] == "open notepad and type hi"
    card["status"] = "done"
    db.save_task(card)                                      # the card in memory has no episode: the row keeps it
    assert db.task_episode("t-1")["app"] == "notepad"
    assert [c["id"] for c in db.load_tasks()] == ["t-1"] and "episode" not in db.load_tasks()[0]
    assert [e["id"] for e in learner.load()] == ["t-1"]     # the Learner reads the same row
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0      # no second copy
        assert con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_old_episodes_move_onto_their_task_rows_once():
    with db.connect() as con:                               # an older Nova's episode and card
        con.execute("INSERT INTO episodes (id, ts, goal, data) VALUES ('a', 1e9, 'old', '{\"id\": \"a\", \"ts\": 2e9, "
                    "\"goal\": \"old\", \"steps\": []}')")
        con.execute("DELETE FROM migrations WHERE version = 3")
    db.close()
    db.migrate()
    assert [e["id"] for e in learner.load()] == ["a"]
    assert db.load_tasks() == []                            # a stub row isn't a card for /tasks


def test_tasks_page_shows_the_record_workers_choices_and_notes():
    card = task_state.new("type hello in notepad", tid="t-9")
    card.update(app_notes="APP NOTES (notepad, 1 earlier run, 1 worked) Worked: press_keys ctrl+s.", replanned=True)
    task_state.note_choice(card, {"questions": [{"key": "folder", "text": "Which folder?"}]},
                           {"folder": {"value": "Documents", "how": "pick"}})
    db.save_task(card)
    learner.record(_ep("type hello in notepad", [_step("open_app", name="notepad"),
                                                 _step("research_in_background", out="Started background job #4", topic="x")],
                       "t-9"))
    from nova.actions import workers
    workers._save_record({"key": "k4", "job": 4, "started": card["started"] + 1, "worker": "opencode", "status": "done",
                          "cost": 0.25, "sent_back": 1})
    d = app.tasks_page.detail("t:t-9")
    assert d["card"]["choices"] == [{"q": "Which folder?", "a": "Documents", "how": "pick"}]
    assert d["card"]["app_notes"].startswith("APP NOTES (notepad")
    r = d["record"]
    assert r["app"] == "notepad" and r["cost"] == 0.25 and r["rounds"] == 3 and r["workers"][0]["id"] == "j:k4"


# ------------------------------------------------------------------ app notes

def test_app_notes_say_what_worked_what_didnt_and_what_got_in_the_way():
    eps = [_ep("save my list in notepad", [_step("open_app", name="notepad"), _step("press_keys", keys="ctrl+s"),
                                           _step("click_text", ok=False, text="Save as")], "a", app_name="notepad"),
           _ep("open the shop in chrome", [_step("open_app", name="chrome"),
                                           _step("read_page", out="We use cookies. Accept all cookies?")], "b")]
    n = appnotes.notes("notepad", eps)
    assert n["runs"] == 1 and n["controls"] == ["press_keys ctrl+s"] and n["failed"] == ['click_text "Save as"']
    assert eps[1]["app"] == "chrome" and eps[1]["blocked"] == ["cookie banner"]
    line = appnotes.describe(appnotes.notes("chrome", eps))
    assert line.startswith("APP NOTES (chrome, 1 earlier run, 1 worked") and "cookie banner (1x)" in line
    assert appnotes.notes("excel", eps) is None and appnotes.line("") == ""
    assert appnotes.blocked([{"out": "Please sign in to continue"}, {"out": "the page is still loading"}]) == \
        ["sign-in wall", "slow load"]


def test_the_second_run_of_an_app_task_has_that_apps_notes_in_its_plan(brain):
    brain([("", [("open_app", {"name": "notepad"})]), ("", [("press_keys", {"keys": "ctrl+n"})]), ("Done!", [])])
    app.answer("open a new page in notepad")
    first = app.state["task"]["card"]
    assert not first.get("app_notes")                       # the first time she knows nothing about it
    brain([("Sure.", [])])
    app.answer("open a new page in notepad again")          # the last task is over: its record is written
    card = app.state["task"]["card"]
    assert card["app_notes"].startswith("APP NOTES (notepad, 1 earlier run")
    assert "press_keys ctrl+n" in card["app_notes"]
    assert card["app_notes"] in task_state.render(card)     # the brain sees it on the card
    assert card["app_notes"] in task_state.plan_goal(app.state["task"])     # and the deep brain's planner


def test_an_app_opened_mid_task_brings_its_notes():
    learner.record(_ep("open calculator", [_step("open_app", name="calculator"), _step("press_keys", keys="2+2")], "c1"))
    card = task_state.new("work out two plus two")
    assert not card.get("app_notes")
    task_state.start(card, {"name": "open_app", "args": {"name": "Calculator"}})
    assert card["app_notes"].startswith("APP NOTES (calculator") and card["app_notes"] in task_state.render(card)


# ------------------------------------------------------------------ lessons from corrections

def test_a_correction_shows_up_as_a_lesson_on_the_daily_list(brain):
    brain([("", [("open_folder", {"path": "C:/Users/me/Reports"})]), ("Opened Reports.", [])])
    app.answer("open my reports folder")
    brain([("Opening the other one.", [])])
    app.answer("no, the other folder")
    qs = [q for q in questions.open_questions() if q["kind"] == "lesson"]
    assert len(qs) == 1
    q = qs[0]
    assert "no, the other folder" in q["text"] and "open my reports folder" in q["text"] and q["why"]
    assert "the other folder" not in knowledge.recall()     # not used before the user ticks it
    app.state["daily_question"] = {"id": q["id"], "until": time.time() + 60}
    said = app.take_question("yes")
    assert "remember" in said
    lesson = knowledge.load()["lessons"][-1]
    assert "no, the other folder" in lesson["lesson"] and lesson["source"] == "user"
    assert questions.get(q["id"])["status"] == "yes"


def test_dont_do_x_and_stop_then_redo_are_lessons():
    last = {"goal": "play some music", "started": time.time() - 60, "trace": [_step("open_app", name="spotify")]}
    got = lessons.from_request(last, "don't open Spotify for music, use YouTube")
    assert got["lesson"] == "Don't open Spotify for music, use YouTube." and got["situation"] == "play some music"
    redo = lessons.from_request({**last, "stopped": True}, "play some music on youtube")
    assert "stopped me at “open_app spotify”" in redo["lesson"] and "play some music on youtube" in redo["lesson"]
    for words in ("don't worry about it", "never mind", "stop the music", "thanks", "play some music"):
        assert lessons.from_request({**last, "stopped": True}, words) is None, words
    assert lessons.from_request({**last, "started": time.time() - 7200}, "no, the other one") is None    # too long ago
    assert lessons.offer("", "Don't type my password is hunter2", "x") is None                      # never anything secret


def test_the_knowledge_page_tick_turns_a_lesson_on(client):
    qid = lessons.offer("play some music", "Don't open Spotify.", "you told me")
    r = client.get("/api/knowledge").get_json()
    assert any(q["id"] == qid and q["kind"] == "lesson" for q in r["questions"])
    assert "Spotify" not in knowledge.recall()
    r = client.post("/api/knowledge", json={"do": "question_yes", "id": qid}).get_json()
    assert r["ok"] and "Spotify" in knowledge.recall()
    assert client.post("/api/knowledge", json={"do": "question_yes", "id": qid}).status_code == 404


def test_the_review_brain_rewords_the_code_lesson_instead_of_saving_it(monkeypatch):
    qid = lessons.offer("open my reports folder", "open_folder was wrong - the user said “no, the other folder”.", "x")
    app.state["lesson_q"] = {"id": qid, "ts": time.time()}
    monkeypatch.setattr(learning.runtime, "spawn", lambda name, fn: fn())
    monkeypatch.setattr(learning.time, "sleep", lambda s: None)
    calls = [{"name": "save_knowledge", "args": {"profile": [], "lessons": [
        {"situation": "opening the reports folder", "lesson": "Reports are in D:\\Work\\Reports, not Documents.",
         "kind": "mistake"}]}}]
    monkeypatch.setattr(learning.llm, "chat", lambda *a, **k: ("", "", calls))
    before = len(knowledge.load()["lessons"])
    REAL_REVIEW(learning.CORRECTED)
    assert len(knowledge.load()["lessons"]) == before        # not saved: asked first
    q = questions.get(qid)
    assert q["status"] == "open" and "D:\\Work\\Reports" in q["text"] and len(questions.open_questions()) == 1


# ------------------------------------------------------------------ the daily question and an open question card

def test_an_open_question_card_doesnt_break_the_daily_question(brain):
    brain([("Hi!", [])])
    # Found live 2026-11-25: both kept their question under session.state["question"], so with a card open (a
    # schedule's "Save it?") every typed message crashed in take_question (KeyError 'until').
    from nova.core.app import asking, background
    card = asking.make("schedule", [asking.question("save", "Save this schedule?", kind="yesno", why="test")])
    app.state["question"] = card
    try:
        assert app.take_question("yes") is None               # the card's "yes" is the card's, not the daily list's
        assert learning.question_line() == ""                 # and no daily question is asked over an open card
        with background.api.app.test_client() as c:
            assert c.post("/api/chat", json={"text": "hello"}).status_code != 500
    finally:
        app.state.pop("question", None)
