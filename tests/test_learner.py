"""Manager plan phase 2: the episode log, the automatic Learner, site facts and the daily question list."""
import itertools
import json
import time

from nova import store
from nova.actions import coworker, learner, questions, skills
from nova.core import app


_TASK_IDS = itertools.count(1)


def _task(goal, trace, verdict=None, **extra):
    # A counter, not the clock: Windows' clock can tick only every ~15 ms, so two tasks made back to back got the same
    # id, record() merged them into one episode and the "same task twice" tests failed now and then on CI.
    t = {"id": extra.pop("id", f"task-{next(_TASK_IDS)}"), "started": time.time() - 5, "goal": goal, "trace": trace}
    if verdict is not None:
        t["verdict"] = {"verdict": verdict, "source": "checks",
                        "results": [{"check": "window_open notepad", "ok": verdict}]}
    t.update(extra)
    return t


def _step(tool, ok=True, out="ok", **args):
    return {"tool": tool, "args": args, "ok": ok, "out": out, "t": time.time()}


NOTEPAD = [_step("open_app", name="notepad"), _step("type_text", text="shopping list")]


# ------------------------------------------------------------------ the episode log

def test_episode_keeps_the_task_and_hides_secrets():
    t = _task("log in and open notepad", [_step("react", name="happy"), _step("open_app", name="notepad"),
                                          _step("type_text", text="my password is hunter2"),
                                          _step("type_text", text="mail me at a@b.com")], verdict=True)
    ep = learner.episode_from_task(t, brain="qwen3:8b")
    assert [s["tool"] for s in ep["steps"]] == ["open_app", "type_text", "type_text"]   # her own moves are left out
    assert ep["steps"][1]["args"]["text"] == learner.HIDDEN and ep["steps"][2]["args"]["text"] == learner.HIDDEN
    assert ep["redacted"] and ep["verdict"] is True and ep["brain"] == "qwen3:8b" and ep["checks"]
    assert "hunter2" not in json.dumps(ep) and "a@b.com" not in json.dumps(ep)


def test_a_chat_with_no_steps_is_not_an_episode():
    assert learner.episode_from_task(_task("tell me a joke", [])) is None
    assert learner.episode_from_task(_task("hi", [_step("react", name="wave")])) is None


def test_episodes_are_updated_in_place_and_old_ones_pruned():
    learner.record({**learner.episode_from_task(_task("old", NOTEPAD, id="old")), "ts": time.time() - 40 * 86400})
    t = _task("open notepad and type shopping list", NOTEPAD[:1], verdict=True, id="t1")
    learner.record(learner.episode_from_task(t))
    t["trace"] = list(NOTEPAD)
    learner.record(learner.episode_from_task(t))
    eps = learner.load()
    assert [e["id"] for e in eps] == ["t1"] and len(eps[0]["steps"]) == 2
    with store.current().db() as con:                                   # the 40-day-old one is gone, not just hidden
        assert con.execute("SELECT id FROM episodes").fetchall() == [("t1",)]


def test_worked_is_strict():
    ep = learner.episode_from_task(_task("x", NOTEPAD, verdict=True))
    assert learner.worked(ep)
    assert learner.worked({**ep, "verdict": None})                # checks covered part of it, all passed
    assert not learner.worked({**ep, "verdict": None, "checks_ok": False})   # nothing could check it: never offered
    assert not learner.worked({**ep, "verdict": None, "steps": [{"tool": "open_app", "ok": False}]})
    assert not learner.worked({**ep, "verdict": False})
    assert not learner.worked({**ep, "stopped": True})
    job_ep = {**ep, "verdict": None, "jobs": [{"id": 3, "status": "done"}]}
    assert learner.worked(job_ep)
    assert not learner.worked({**job_ep, "jobs": [{"id": 3, "status": "failed"}]})


# ------------------------------------------------------------------ the Learner's skill offers

def test_a_task_that_worked_is_offered_once_as_a_skill():
    learner.record(learner.episode_from_task(_task("open notepad and type my shopping list", NOTEPAD, verdict=True)))
    made = learner.learn()
    assert len(made) == 1
    q = questions.get(made[0])
    assert q["kind"] == "save_skill" and "open notepad and type my shopping list" in q["text"] and q["why"]
    assert [s["tool"] for s in q["data"]["steps"]] == ["open_app", "type_text"]
    assert learner.learn() == []                                   # learned once: not offered again
    learner.record(learner.episode_from_task(_task("open notepad and type my shopping list", NOTEPAD, verdict=True)))
    assert learner.learn() == []                                   # the same task again: same question key


def test_no_offer_for_unchecked_failed_one_step_or_skill_tasks():
    learner.record(learner.episode_from_task(_task("open notepad and type a", NOTEPAD)))              # no verdict
    learner.record(learner.episode_from_task(_task("open notepad and type b", NOTEPAD, verdict=False)))
    learner.record(learner.episode_from_task(_task("open notepad", NOTEPAD[:1], verdict=True)))      # one step
    learner.record(learner.episode_from_task(_task("movie time", NOTEPAD, verdict=True, ran_skill="movie-time")))
    learner.record(learner.episode_from_task(_task("open my notes", NOTEPAD, verdict=True)))
    assert learner.learn(is_skill=lambda goal: goal == "open my notes") == []


def test_secret_steps_are_never_offered():
    t = _task("type my password", [_step("open_app", name="notepad"), _step("type_text", text="password: x1")],
              verdict=True)
    learner.record(learner.episode_from_task(t))
    assert learner.learn() == []


def _research(goal, topic, job_id):
    return _task(goal, [_step("research_in_background", out=f"Started background job #{job_id}: researching", topic=topic)])


def test_the_same_kind_of_task_twice_offers_a_skill_with_an_input_slot():
    learner.record(learner.episode_from_task(_research("search for tea", "tea", 1)))
    learner.job_finished({"id": 1, "kind": "research", "status": "done", "title": "tea"})
    assert learner.learn() == []                  # one research step, once: just a request
    learner.record(learner.episode_from_task(_research("search for coffee", "coffee", 2)))
    assert learner.learn() == []                  # its job is still running: not learned from yet
    learner.job_finished({"id": 2, "kind": "research", "status": "done", "title": "coffee"})
    made = learner.learn()
    assert len(made) == 1
    q = questions.get(made[0])
    assert "search for" in q["text"] and q["data"]["examples"] == ["search for"]
    assert q["data"]["steps"][0]["args"] == {"topic": "{input}"}


def test_template_needs_one_changing_part():
    a = learner.episode_from_task(_research("search for tea", "tea", 1))
    b = learner.episode_from_task(_research("search for coffee", "coffee beans", 2))
    assert learner.template(a, b) is None                         # the argument isn't the changed words
    c = learner.episode_from_task(_research("look up coffee", "coffee", 3))
    assert learner.template(a, c) is None                         # no shared trigger words
    d = learner.episode_from_task(_task("open notepad and type x", NOTEPAD))
    assert learner.template(a, d) is None                         # different steps


def test_a_failed_research_job_teaches_nothing():
    learner.record(learner.episode_from_task(_research("search for tea", "tea", 1)))
    learner.job_finished({"id": 1, "kind": "research", "status": "done", "title": "tea"})
    learner.record(learner.episode_from_task(_research("search for coffee", "coffee", 2)))
    learner.job_finished({"id": 2, "kind": "research", "status": "failed", "title": "coffee"})
    assert learner.learn() == []


# ------------------------------------------------------------------ facts: the sites research really used

def test_research_sites_are_learned_and_read_back():
    learner.learn_sites("tea", ["https://www.teabox.com/a", "https://arena.org/b", "https://junk.net/c"],
                        ["https://www.teabox.com/a", "https://arena.org/b"])
    prefer, avoid, used = learner.sites_for("tea")
    assert set(prefer) == {"teabox.com", "arena.org"} and avoid == ["junk.net"] and len(used) == 1
    assert learner.sites_for("quantum batteries") == ([], [], [])
    assert learner.sites_for("green tea benefits")[0] == []       # too different a topic
    text = learner.describe_fact(used[0])
    assert "teabox.com" in text and "junk.net was opened and ignored" in text
    f = learner.load_facts()["facts"][0]
    assert f["confirmed"] is False
    learner.confirm_fact(f["id"])
    assert learner.load_facts()["facts"][0]["confirmed"] is True
    assert learner.delete_fact(f["id"]) == 1 and learner.sites_for("tea")[0] == []


def test_rerank_puts_good_sites_first_and_drops_ignored_ones():
    links = [("J", "https://junk.net/1"), ("O", "https://other.com/2"), ("T", "https://shop.teabox.com/3"),
             ("X", "https://x.com/4")]
    assert learner.rerank(links, ["teabox.com"], ["junk.net"], keep=3) == [links[2], links[1], links[3]]
    assert learner.rerank(links[:2], [], ["junk.net"], keep=3) == [links[1], links[0]]    # too few: kept, last


def _fake_research(monkeypatch, tmp_path, results, done, cites="[1]"):
    opened = []
    monkeypatch.setattr(coworker.tools, "HOME", tmp_path)
    monkeypatch.setattr(coworker, "_browser", lambda: ("chrome.exe", "chrome"))
    monkeypatch.setattr(coworker, "search", lambda q, p, e, w: results(q))
    monkeypatch.setattr(coworker, "_open_parked", lambda url, *a, **k: (opened.append(url), url)[1])
    monkeypatch.setattr(coworker, "close_window", lambda h: None)
    monkeypatch.setattr(coworker, "_title", lambda h: "A page - Google Chrome")
    monkeypatch.setattr(coworker.tools, "page_text", lambda h, n=0: "tea is a drink made from leaves. " * 20)
    coworker.configure(write=lambda p, n: f"Headline: Tea is good.\n\n1. Leaves matter {cites}", on_done=done.append)
    return opened


def _wait(fn):
    for _ in range(200):
        if fn():
            return True
        time.sleep(0.02)
    return False


def test_second_research_on_a_topic_reads_the_remembered_sites_first(monkeypatch, tmp_path):
    """Plan phase 2 'done when': the second 'search for tea' uses the remembered sites."""
    first = [("Junk", "https://junk.net/tea"), ("Good", "https://teabox.com/tea"), ("Meh", "https://meh.com/tea")]
    done = []
    _fake_research(monkeypatch, tmp_path, lambda q: first, done, cites="[2]")
    job, _ = coworker.research("tea", max_sources=3)
    assert _wait(lambda: done) and done[0]["status"] == "done", done and done[0]["error"]
    assert done[0]["cited"] == ["https://teabox.com/tea"]
    learner.job_finished(done[0])                    # what app.job_done does
    assert learner.sites_for("tea")[0] == ["teabox.com"]
    # the second time: the good site is read first, the ignored ones are skipped while there are enough others
    second = [("Junk", "https://junk.net/t2"), ("New", "https://new.com/t2"), ("Good", "https://teabox.com/t2"),
              ("Meh", "https://meh.com/t2"), ("Other", "https://other.com/t2")]
    done2 = []
    opened = _fake_research(monkeypatch, tmp_path, lambda q: second, done2)
    coworker.research("tea", max_sources=3)
    assert _wait(lambda: done2) and done2[0]["status"] == "done"
    assert opened[0] == "https://teabox.com/t2"
    assert not any("junk.net" in u or "meh.com" in u for u in opened)
    coworker.configure()


def test_a_remembered_site_missing_from_results_is_searched_for(monkeypatch, tmp_path):
    learner.learn_sites("tea", ["https://teabox.com/a"], ["https://teabox.com/a"])
    asked, done = [], []
    def results(q):
        asked.append(q)
        return [("Good", "https://teabox.com/z")] if "site:teabox.com" in q else [("New", "https://new.com/z")]
    opened = _fake_research(monkeypatch, tmp_path, results, done)
    coworker.research("tea", max_sources=2)
    assert _wait(lambda: done) and done[0]["status"] == "done"
    assert asked == ["tea", "tea site:teabox.com"] and opened[0] == "https://teabox.com/z"
    coworker.configure()


# ------------------------------------------------------------------ the daily question list

def test_questions_are_asked_once_a_day_one_at_a_time():
    now = 1_000_000.0
    a = questions.add("save_skill", "Save A?", "it worked", key="a", now=now)
    b = questions.add("save_skill", "Save B?", "it worked", key="b", now=now)
    assert questions.add("save_skill", "Save A again?", "x", key="a", now=now) is None
    assert questions.due(now)["id"] == a
    questions.mark_asked(a, now)
    assert questions.due(now + 60)["id"] == b               # the day's list goes on after the first
    questions.mark_asked(b, now + 60)
    assert questions.due(now + 120) is None                 # both asked today (A was skipped): nothing more today
    assert questions.due(now + 3600 * 5) is None            # the day's list is over
    assert questions.due(now + 3600 * 21)["id"] == a        # another day: the skipped one comes back
    questions.answer(a, "no")
    assert questions.due(now + 3600 * 21)["id"] == b


def test_a_question_skipped_three_times_is_not_asked_again():
    now = 1_000_000.0
    q = questions.add("save_skill", "Save?", "why", key="k", now=now)
    for day in range(questions.MAX_ASKS):
        t = now + day * 86400
        assert questions.due(t)["id"] == q
        questions.mark_asked(q, t)
    assert questions.due(now + 10 * 86400) is None


def test_urgent_questions_skip_the_daily_wait():
    now = 1_000_000.0
    questions.add("save_skill", "Save?", "why", key="k", now=now)
    questions.mark_asked(questions.due(now)["id"], now)
    u = questions.add("blocked", "The worker needs an OK", "it's stuck", key="u", urgent=True, now=now)
    assert questions.due(now + 30)["id"] == u


# ------------------------------------------------------------------ in the app

def test_finished_tasks_go_to_the_episode_log(brain):
    brain([("", [("open_app", {"name": "notepad"})]), ("Opened Notepad!", [])])
    app.answer("open notepad")
    app.answer("thanks")                         # the next request: the last task is over
    eps = learner.load()
    assert [e["goal"] for e in eps] == ["open notepad"] and eps[0]["steps"][0]["tool"] == "open_app"


def test_idle_learner_records_the_current_task_and_queues_an_offer(brain, monkeypatch):
    monkeypatch.setattr(app.checks, "evaluate", lambda chk, ctx, **k: (True, "it is open"))
    brain([("", [("open_app", {"name": "notepad"})]), ("", [("open_app", {"name": "calculator"})]),
           ("Both are open.", [])])
    app.answer("open notepad and calculator")
    assert app.state["task"]["verdict"]["verdict"] is True        # the Judge's checks cover the whole goal
    app.nap["last_task"] = time.time() - 120
    assert app.learn_due()
    made = app.learn_now()
    assert len(made) == 1 and len(learner.load()) == 1
    assert not app.learn_due()                    # not again for a while


def test_daily_question_is_asked_after_an_answer_and_yes_saves_the_skill(brain):
    learner.record(learner.episode_from_task(_research("search for tea", "tea", 1)))
    learner.job_finished({"id": 1, "kind": "research", "status": "done", "title": "tea"})
    learner.record(learner.episode_from_task(_research("search for coffee", "coffee", 2)))
    learner.job_finished({"id": 2, "kind": "research", "status": "done", "title": "coffee"})
    assert learner.learn()
    brain([("It's 3 pm.", [])])
    out, _ = app.answer("what time is it")
    assert "One question from my learning" in out["reply"] and "search for" in out["reply"]
    assert "I'm asking because" in out["reply"]
    said = app.take_question("yes")
    assert said.startswith("Saved a draft skill 'search-for'")
    s = skills.get("search-for")
    assert s and s["status"] == "draft" and s["needs_input"]
    assert app.take_question("yes") is None        # answered: nothing waiting
    brain([("Sure.", [])])
    out, _ = app.answer("ok thanks")
    assert "One question" not in out["reply"]       # once a day


def test_no_to_the_question_and_other_words_leave_it(brain):
    questions.add("save_skill", "Save it?", "it worked", data={}, key="k")
    brain([("Hi!", [])])
    out, _ = app.answer("hello")
    assert "Save it?" in out["reply"]
    assert app.take_question("what's the weather like today in delhi and mumbai") is None   # not an answer
    assert app.take_question("no") == "Okay, I won't."
    assert questions.get(1)["status"] == "no" and not skills.load()


def test_phone_requests_never_get_the_daily_question(brain):
    questions.add("save_skill", "Save it?", "it worked", key="k")
    brain([("Hi!", [])])
    out, _ = app.answer("hello", quiet=True, remote=True)
    assert "Save it?" not in out["reply"]


def test_chat_api_answers_the_question(brain, client):
    questions.add("note", "Is that right?", "I'm not sure", key="k")
    brain([("Hi!", [])])
    client.post("/api/chat", json={"text": "hello"})
    r = client.post("/api/chat", json={"text": "yes"}).get_json()
    assert r["reply"] == "Thanks - noted." and questions.get(1)["status"] == "yes"


def test_job_done_feeds_the_learner(monkeypatch):
    called = []
    monkeypatch.setattr(app.learner, "job_finished", called.append)
    app.job_done({"id": 9, "kind": "research", "status": "failed", "error": "x", "title": "tea"})
    assert called and called[0]["id"] == 9


def test_knowledge_page_shows_and_deletes_facts_and_questions(client):
    learner.learn_sites("tea", ["https://teabox.com/a"], ["https://teabox.com/a"])
    questions.add("save_skill", "Save it?", "it worked", key="k")
    k = client.get("/api/knowledge").get_json()
    assert "teabox.com" in k["facts"][0]["text"] and not k["facts"][0]["confirmed"]
    assert k["questions"][0]["text"] == "Save it?"
    fid, qid = k["facts"][0]["id"], k["questions"][0]["id"]
    assert client.post("/api/knowledge", json={"do": "fact_confirm", "id": fid}).get_json()["ok"]
    assert client.post("/api/knowledge", json={"do": "question_drop", "id": qid}).get_json()["ok"]
    assert client.post("/api/knowledge", json={"do": "fact_delete", "id": "x"}).status_code == 400
    k = client.get("/api/knowledge").get_json()
    assert k["facts"][0]["confirmed"] and k["questions"] == []
    client.post("/api/knowledge", json={"do": "fact_delete", "id": fid})
    assert client.get("/api/knowledge").get_json()["facts"] == []


def test_web_research_is_traced_as_the_background_job_that_ran(brain):
    brain([("", [("web_research", {"query": "tea"})]), ("On it.", [])],
          tool_output="Started background job #1: researching 'tea'")
    app.answer("search for tea")
    assert [t["tool"] for t in app.state["task"]["trace"]] == ["research_in_background"]
