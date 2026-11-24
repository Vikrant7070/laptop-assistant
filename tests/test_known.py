"""Manager plan 4d-2: the task card knows things (given in the request, found on the way, guesses) and questions don't
stop the work - answered from what is known when they can be, otherwise gathered on one card while other steps go on."""
import json

import pytest

from nova import known, store, task_state
from nova.actions import manager, tools
from nova.actions.tools import taskcard
from nova.core import app
from nova.core.app import session, watching


@pytest.fixture
def trusted():
    s = app.settings()
    s["autonomy"] = "trusted"
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")


@pytest.fixture
def card(monkeypatch):
    c = task_state.new('put "weekly report" in C:/Users/me/Docs and send it to boss@work.example')
    monkeypatch.setattr(known, "current_card", lambda: c)
    return c


# ------------------------------------------------------------------ the known list (pure)

def test_facts_given_in_the_request_are_on_a_new_card(card):
    assert {k["key"]: k["value"] for k in card["known"]} == {
        "path": "C:/Users/me/Docs", "email": "boss@work.example", "quoted": "weekly report"}
    assert all(k["how"] == "given" for k in card["known"])
    assert "KNOWN: path=C:/Users/me/Docs; email=boss@work.example; quoted=weekly report" in task_state.render(card)


def test_found_guess_and_lookup(card):
    known.add(card, "Repo URL", "https://github.com/x/y", "found")
    assert known.lookup(card, "repo_url")["value"] == "https://github.com/x/y"
    known.add(card, "repo_url", "https://other", "guess")                   # a guess never replaces a fact
    assert known.lookup(card, "repo_url")["value"] == "https://github.com/x/y"
    known.add(card, "branch", "main", "guess")
    assert known.lookup(card, "branch") is None                             # guesses never answer a question
    assert "branch=main (a guess)" in "\n".join(known.render(card))
    assert known.lookup(card, "email")["value"] == "boss@work.example"
    assert known.lookup(card, "url")["key"] == "repo_url"                   # the key's words inside a fact's key


def test_secrets_never_go_on_the_card(card):
    known.add(card, "login", "the password is hunter2", "found")
    assert "hunter2" not in json.dumps(card["known"])


# ------------------------------------------------------------------ the tools

def test_ask_user_answers_from_what_is_known_first(card):
    out = taskcard.ask_user("Where should I save it?", "path")
    assert out.startswith("Already known - don't ask the user: path = C:/Users/me/Docs") and not known.pending(card)
    manager.remember("github_username", "vik-dev")                          # told me in an earlier task
    out = taskcard.ask_user("What's your GitHub username?", "github_username")
    assert "vik-dev" in out and "what the user told me before" in out
    assert known.lookup(card, "github_username")["how"] == "found"


def test_ask_user_queues_and_the_work_goes_on(card):
    out = taskcard.ask_user("Which subject line?", "subject", why="The mail needs one.")
    assert out.startswith("Queued for the user - NOT asked yet. Carry on now")
    assert taskcard.ask_user("Which subject line?", "subject").startswith("Queued")    # once on the card
    assert [a["key"] for a in known.pending(card)] == ["subject"]
    assert "WAITING FOR THE USER'S ANSWER: Which subject line?" in task_state.render(card)
    assert "end your turn now" in taskcard.ask_user("Which account?", "account", needed_now=True)
    assert taskcard.note_fact("draft_id", "42") == "Noted on the task card: draft_id = 42."
    assert taskcard.note_fact("cc", "nobody", guess=True).endswith("(a guess).")


def test_without_a_task_the_tools_say_so(monkeypatch):
    monkeypatch.setattr(known, "current_card", lambda: None)
    assert "ask the user in your reply" in taskcard.ask_user("x?", "x")
    assert "nothing to note" in taskcard.note_fact("a", "b")


# ------------------------------------------------------------------ in the agent loop

def _real_card_tools(monkeypatch, ran):
    """The FakeBrain's tool recorder, but ask_user / note_fact really run (they only touch the card)."""
    monkeypatch.setattr(tools, "run_tool", lambda n, a: (ran.append((n, a)), tools.FUNCS[n](**a) if n in (
        "ask_user", "note_fact") else "ok")[1])


def test_a_question_waits_while_other_steps_go_on_then_one_card_asks(brain, trusted, monkeypatch):
    fake, ran = brain([
        ("", [("ask_user", {"question": "What's your GitHub username?", "key": "github_username"})]),
        ("", [("ask_user", {"question": "Public or private repo?", "key": "visibility"})]),
        ("", [("open_website", {"url": "https://github.com/new"})]),          # a step that doesn't need the answers
        ("The page is open; I need two answers to go on.", []),
    ])
    _real_card_tools(monkeypatch, ran)
    out, _ = app.answer("make a new GitHub repo for my notes")
    assert [n for n, _ in ran] == ["ask_user", "ask_user", "open_website"]  # the work went on after the questions
    q = out["question"]
    assert [x["key"] for x in q["questions"]] == ["github_username", "visibility"]      # both on one card
    assert "The page is open" in out["reply"] and "What's your GitHub username?" in out["reply"]
    card = task_state.of(app.state["task"])
    assert card["status"] == "waiting" and [a["state"] for a in card["asks"]] == ["asked", "asked"]
    # the answers: on the card as given, saved for next time, and the task carries on
    assert watching._gathered(session.state["question"], {"github_username": {"value": "vik-dev"},
                                                          "visibility": {"value": "", "how": "skip"}}) is None
    assert known.lookup(card, "github_username") == {"key": "github_username", "value": "vik-dev", "how": "given"}
    assert manager.recall("github_username") == "vik-dev"
    extra = session.state["resume_next"]["extra"][-1]
    assert "github_username = vik-dev" in extra and "visibility: the user skipped it" in extra
    assert not known.pending(card)


def test_the_saved_answer_is_used_next_time_without_asking(brain, trusted, monkeypatch):
    manager.remember("github_username", "vik-dev")
    fake, ran = brain([
        ("", [("ask_user", {"question": "What's your GitHub username?", "key": "github_username"})]),
        ("Your username is vik-dev, so I used it.", []),
    ])
    _real_card_tools(monkeypatch, ran)
    out, _ = app.answer("open my GitHub profile")
    assert "question" not in out and out["reply"] == "Your username is vik-dev, so I used it."


def test_an_unanswered_card_asks_again_when_the_task_carries_on(card, monkeypatch):
    monkeypatch.setattr(app.resume, "_find", lambda cid: card)
    known.queue(card, "subject", "Which subject line?")
    session.state["task"] = {"card": card, "goal": card["goal"], "steps": 1}
    asked = watching.gather(session.state["task"])
    assert asked["question"]["questions"][0]["key"] == "subject" and card["asks"][0]["state"] == "asked"
    watching._gather_dropped(session.state["question"])
    assert card["asks"][0]["state"] == "open" and card["status"] == "stopped"
