"""Question cards (manager plan 4-pre e): every kind of control, words and taps, skip, Other…, the phone."""
import json
import time

import pytest

from nova import db, task_state as ts
from nova.actions import phone as phone_mod
from nova.core import app, mobile as mobile_mod
from nova.core.app import asking, resume

GOT = []


def _answered(card, answers):
    GOT.append(answers)
    return {"reply": f"got {json.dumps(answers, sort_keys=True)}", "say": "ok", "activity": ["Got it"], "by_code": True}


DROPPED = []
asking.handler("test", _answered, lambda card: DROPPED.append(card["id"]))


@pytest.fixture(autouse=True)
def fresh():
    GOT.clear()
    DROPPED.clear()
    yield
    app.state.pop("question", None)


def _ask(*questions, title="", task=None):
    card = asking.make("test", list(questions), title=title, task=task)
    out = asking.ask(card)
    return card, out


FOLDERS = asking.question("folder", "Which folder?", "one", choices=[("a", "Laptop assistant"), ("b", "nova-site")],
                          why="So the worker edits the right project.", other=True, guess="a", last="b")


# --- the card itself ------------------------------------------------------------------------------------------

def test_the_card_reads_aloud_with_numbered_choices():
    card, out = _ask(FOLDERS)
    assert out["reply"] == "Which folder? one: Laptop assistant, two: nova-site."
    assert out["question"]["questions"][0]["last"] == "b" and "data" not in out["question"]
    with pytest.raises(ValueError):
        asking.make("test", [FOLDERS, FOLDERS, FOLDERS])                       # at most 2 questions


# --- taps -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("q,given,want", [
    (asking.question("go", "Start?", "yesno", choices=[("go", "Go"), ("change", "Change it")]), "change",
     {"value": "change", "how": "tap"}),
    (FOLDERS, "b", {"value": "b", "how": "tap"}),
    (asking.question("parts", "What should the site have?", "many",
                     choices=[("home", "Home"), ("docs", "Docs"), ("dl", "Download")]), ["dl", "home"],
     {"value": ["home", "dl"], "how": "tap"}),
    (asking.question("name", "What should the site be called?", "text"), "Nova site",
     {"value": "Nova site", "how": "tap"}),
    (FOLDERS, {"other": "C:/work/blog"}, {"value": "C:/work/blog", "how": "other"}),
    (FOLDERS, {"skip": True}, {"value": "a", "how": "skip"}),
])
def test_each_control_sends_the_right_answer(client, q, given, want):
    card, _ = _ask(q)
    r = client.post("/api/answer", json={"id": card["id"], "answers": {q["key"]: given}})
    assert r.status_code == 200 and GOT == [{q["key"]: want}]
    assert "question" not in app.state
    asked = [m["content"] for m in app.state["messages"] if m["role"] == "user"]
    assert asked and not asked[-1].startswith("{")                           # the history reads like words


@pytest.mark.parametrize("given", ["c", ["a", "zzz"], {"other": "x"}])
def test_only_offered_answers_get_through(client, given):
    q = asking.question("go", "Start?", "yesno") if given == {"other": "x"} else FOLDERS
    if isinstance(given, list):
        q = asking.question("parts", "Which?", "many", choices=[("a", "A"), ("b", "B")])
    card, _ = _ask(q)
    r = client.post("/api/answer", json={"id": card["id"], "answers": {q["key"]: given}})
    assert r.status_code == 400 and GOT == [] and app.state.get("question")


def test_a_question_without_a_guess_cant_be_skipped_and_an_old_card_expires(client):
    card, _ = _ask(asking.question("go", "Start?", "yesno"))
    assert client.post("/api/answer", json={"id": card["id"], "answers": {"go": {"skip": True}}}).status_code == 400
    assert client.post("/api/answer", json={"id": "q-old", "answers": {"go": "yes"}}).status_code == 410
    app.state["question"]["ts"] -= asking.ASK_SECS + 1
    assert client.post("/api/answer", json={"id": card["id"], "answers": {"go": "yes"}}).status_code == 410
    assert DROPPED == [card["id"]]


# --- words ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("said,want", [
    ("two", {"value": "b", "how": "words"}),
    ("the second one", {"value": "b", "how": "words"}),
    ("[spoken] nova-site", {"value": "b", "how": "words"}),
    ("laptop assistant please", {"value": "a", "how": "words"}),
    ("other: C:/work/blog", {"value": "C:/work/blog", "how": "other"}),
    ("you decide", {"value": "a", "how": "skip"}),
])
def test_typed_and_spoken_answers_work(brain, said, want):
    _ask(FOLDERS)
    fake, _ = brain([])
    out, _ = app.answer(said)
    assert GOT == [{"folder": want}] and fake.seen == []                      # code answered, no brain turn


def test_yes_no_and_several_by_words(brain):
    brain([])
    _ask(asking.question("go", "Start?", "yesno", choices=[("go", "Go"), ("change", "Change it")]))
    app.answer("yes, go")
    _ask(asking.question("parts", "Which parts?", "many", choices=[("home", "Home"), ("docs", "Docs"), ("dl", "Download")]))
    app.answer("one and three")
    _ask(asking.question("parts", "Which parts?", "many", choices=[("home", "Home"), ("docs", "Docs")]))
    app.answer("all of them")
    assert [g[k]["value"] for g in GOT for k in g] == ["go", ["home", "dl"], ["home", "docs"]]


def test_words_that_dont_answer_it_drop_the_card_and_go_on_as_a_new_request(brain):
    card, _ = _ask(FOLDERS)
    fake, _ = brain([("It's sunny.", [])])
    out, _ = app.answer("what's the weather like")
    assert out["reply"] == "It's sunny." and GOT == [] and DROPPED == [card["id"]] and "question" not in app.state


def test_two_questions_words_answer_the_first_and_the_second_takes_its_guess(brain):
    brain([])
    second = asking.question("style", "A style?", "one", choices=[("plain", "Plain"), ("bold", "Bold")], guess="plain")
    _ask(FOLDERS, second)
    app.answer("two")
    assert GOT == [{"folder": {"value": "b", "how": "words"}, "style": {"value": "plain", "how": "skip"}}]


# --- the resume questions are cards now -----------------------------------------------------------------------

def _saved(goal, age=0, half=False):
    card = ts.new(goal, tid=f"{time.time() - age:.6f}")
    ts.record(card, {"name": "get_system_status", "args": {}}, "Disk 77%", True)
    if half:
        rid = ts.start(card, {"name": "type_text", "args": {"text": "Dear Sam"}})
        ts.leave_open(card, rid)
    ts.mark(card, "stopped", "Stop pressed")
    card["updated"] -= age
    db.save_task(card)
    return card


def test_which_task_is_a_card_and_a_tap_picks_it(brain, client):
    first = _saved("sort my photos", age=60)
    _saved("sort my downloads")
    brain([])
    out, _ = app.answer("continue")
    q = out["question"]["questions"][0]
    assert q["kind"] == "one" and [c["label"] for c in q["choices"]] == ["sort my downloads", "sort my photos", "Not now"]
    fake, _ = brain([("All sorted.", [])])
    r = client.post("/api/answer", json={"id": out["question"]["id"], "answers": {"task": first["id"]}}).get_json()
    assert r["reply"] == "All sorted." and app.state["task"]["goal"] == "sort my photos"


def test_the_redo_question_is_a_yes_no_card_on_the_strip(brain, client):
    card = _saved("write a letter", half=True)
    brain([])
    out, _ = app.answer("continue")
    q = out["question"]
    assert [c["label"] for c in q["questions"][0]["choices"]] == ["Do it again", "Skip that step"] and q["task"] == card["id"]
    assert client.get("/api/task").get_json().get("question", {}).get("id") == q["id"] or \
        resume.strip_for(resume._find(card["id"]))["question"]["id"] == q["id"]
    fake, _ = brain([("Skipped it.", [])])
    client.post("/api/answer", json={"id": q["id"], "answers": {"redo": "no"}})
    assert "NOT to redo" in [m["content"] for m in fake.seen[0] if m["role"] == "user"][-1]


def test_the_restart_offer_is_a_card(brain, client):
    card = _saved("clean my laptop")
    card["status"] = "running"
    db.save_task(card)
    app.state.update(messages=[])
    app.state.pop("task", None)
    resume.on_start()
    q = client.get("/api/question").get_json()
    assert [c["label"] for c in q["questions"][0]["choices"]] == ["Continue", "Not now"]
    brain([])
    r = client.post("/api/answer", json={"id": q["id"], "answers": {"go": "no"}}).get_json()
    assert r["reply"].startswith("Okay, I'll leave it")


# --- the phone ------------------------------------------------------------------------------------------------

def test_telegram_gets_a_button_per_choice_and_a_tap_answers(monkeypatch):
    sent, answered = [], []
    ph = phone_mod.Phone(on_answer=lambda qid, a: answered.append((qid, a)) or {"reply": "ok"})
    monkeypatch.setattr(ph, "send", lambda text, buttons=None, rows=None: sent.append((text, rows)) or 77)
    monkeypatch.setattr(ph, "_call", lambda *a, **k: {})
    monkeypatch.setattr(ph, "_typing", lambda: None)
    monkeypatch.setattr(phone_mod, "_load", lambda: {"chat_id": 5, "user_id": 9})
    card, out = _ask(FOLDERS)
    ph.reply(out)
    assert sent[-1][0] == out["reply"] and [r[0]["text"] for r in sent[-1][1]] == ["Laptop assistant", "nova-site"]
    assert ph._button({"id": "x", "from": {"id": 9}, "message": {"message_id": 76}, "data": "q:1"}) == "expired"
    assert ph._button({"id": "x", "from": {"id": 9}, "message": {"message_id": 77}, "data": "q:1"}) == "answered b"
    ph._work.get()()
    assert answered == [(card["id"], {"folder": "b"})]
    assert ph._button({"id": "x", "from": {"id": 9}, "message": {"message_id": 77}, "data": "q:0"}) == "expired"   # once


def test_the_iphone_app_answers_with_its_key():
    got = []
    mob = mobile_mod.Mobile(on_answer=lambda qid, a: got.append((qid, a)) or {"reply": "ok"})
    c = mob.app.test_client()
    assert c.post("/m/answer", json={"id": "q1", "answers": {"k": "v"}}).status_code == 401
    mob.new_pair_code()
    key, _ = mob.pair(mob.pair_code, "iPhone")
    assert c.post("/m/answer", json={"id": "q1", "answers": {"k": "v"}}, headers={"Authorization": "Bearer " + key}).status_code == 202
    mob.work.get()()
    assert got == [("q1", {"k": "v"})]
    mob.say({"reply": "Which?", "question": {"id": "q2", "questions": []}})
    assert mob.since(0)[-1]["kind"] == "question"
    mobile_mod._file().unlink(missing_ok=True)


def test_a_phone_tap_goes_through_the_same_check(monkeypatch):
    card, _ = _ask(FOLDERS)
    monkeypatch.setattr(app.voice_io, "push_event", lambda e: None)
    out = app.integrations.card_answer(card["id"], {"folder": "zzz"}, "phone")
    assert "error" in out and GOT == []


def test_nothing_secret_on_a_card():
    q = asking.question("pw", "Is your password hunter2?", "yesno")
    assert "hunter2" not in json.dumps(q)


def test_the_pages_are_wired_up():
    static = app.ROOT / "static"
    qjs = (static / "question.js").read_text(encoding="utf-8")
    assert "innerHTML" not in qjs and "flex-wrap" in (static / "nova.css").read_text(encoding="utf-8")
    for page in ("index.html", "tasks.html"):
        html = (static / page).read_text(encoding="utf-8")
        assert html.index("question.js") < html.index("nova.js" if page == "index.html" else "tasks.js")
    nova = (static / "nova.js").read_text(encoding="utf-8")
    assert 'on("question", questionClosed)' in nova and "/api/answer" in nova and "/api/question" in nova
    assert "ts-qa" in (static / "taskstrip.js").read_text(encoding="utf-8")
    assert "/api/answer" in (static / "tasks.js").read_text(encoding="utf-8")
    mob = (static / "mobile" / "index.html").read_text(encoding="utf-8")
    assert '"/m/answer"' in mob and "innerHTML" not in mob


def test_the_strip_shows_the_task_a_question_is_about(client, brain):
    _saved("sort my photos", age=60)
    card = _saved("write a letter", half=True)
    brain([])
    app.answer("carry on")                                       # two stopped: which one?
    client.post("/api/answer", json={"id": app.state["question"]["id"], "answers": {"task": card["id"]}})
    j = client.get("/api/task").get_json()
    assert j["id"] == card["id"] and j["status"] == "waiting"
    assert [c["label"] for c in j["question"]["questions"][0]["choices"]] == ["Do it again", "Skip that step"]
