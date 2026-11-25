"""Manager plan Phase 6: reading what is already on the laptop (Chrome / PowerShell / Claude Code history) into
unconfirmed facts, locally, with a secret filter, a never list, per-source switches and no network. Fixture files only."""
import json
import socket
import sqlite3

import pytest

from nova.actions import laptop_history as lh
from nova.actions import learner

CHROME_1601 = 11644473600


def _chrome_db(path, urls):
    """A tiny Chrome History database: urls + visits tables like the real one."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    con.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER)")
    for i, u in enumerate(urls, 1):
        con.execute("INSERT INTO urls VALUES (?, ?, '')", (i, u))
        con.execute("INSERT INTO visits (url, visit_time) VALUES (?, ?)", (i, (1_790_000_000 + i + CHROME_1601) * 1_000_000))
    con.commit()
    con.close()


def _add_visits(path, urls, start):
    con = sqlite3.connect(path)
    for j, u in enumerate(urls):
        cur = con.execute("INSERT INTO urls (url, title) VALUES (?, '')", (u,))
        con.execute("INSERT INTO visits (url, visit_time) VALUES (?, ?)", (cur.lastrowid, (start + j + CHROME_1601) * 1_000_000))
    con.commit()
    con.close()


@pytest.fixture
def sources(tmp_path, monkeypatch):
    chrome = tmp_path / "Default" / "History"
    chrome.parent.mkdir()
    _chrome_db(chrome, ["https://github.com/a", "https://github.com/b", "https://www.github.com/c",
                        "https://secret-bank.example/acct", "https://secret-bank.example/x", "https://secret-bank.example/y",
                        "https://www.google.com/search?q=flask+blueprints", "https://www.google.com/search?q=flask+login",
                        "https://www.google.com/search?q=my+otp+is+482913", "https://www.google.com/search?q=hunter2+password+reset",
                        "file:///C:/Users/me/private.pdf", "chrome://settings"])
    ps = tmp_path / "ConsoleHost_history.txt"
    ps.write_text("git status\ngit push\ngit pull\npython -m pytest\npython app.py\npython x.py\n"
                  "mysql -u root -p hunter2\n$env:OPENAI_API_KEY='sk-abcdefghijklmnop'\n"
                  "curl -H 'Authorization: Bearer abcdefghij' https://x\ncd C:\\Work\\Private\\thing\n", encoding="utf-8")
    claude = tmp_path / "projects"
    proj = claude / "C--Users-me-Laptop-assistant"
    proj.mkdir(parents=True)
    lines = [
        {"type": "user", "cwd": "C:\\Users\\me\\Laptop assistant", "timestamp": "2026-11-20T10:00:00Z",
         "message": {"role": "user", "content": "Fix the login bug. I prefer small PRs with one change each."}},
        {"type": "assistant", "cwd": "C:\\Users\\me\\Laptop assistant", "message": {"role": "assistant",
                                                                                     "content": [{"type": "text", "text": "I always do that."}]}},
        {"type": "user", "cwd": "C:\\Users\\me\\Laptop assistant\\.claude\\worktrees\\x", "timestamp": "2026-11-21T10:00:00Z",
         "message": {"role": "user", "content": [{"type": "text", "text": "Always use my password hunter2 for the test login."},
                                                  {"type": "tool_result", "content": "I prefer tool output"}]}},
        {"type": "user", "isMeta": True, "message": {"content": "<command-name>always meta</command-name>"}},
    ]
    (proj / "s1.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    priv = claude / "C--Work-Private"
    priv.mkdir()
    (priv / "s2.jsonl").write_text(json.dumps({"type": "user", "cwd": "C:\\Work\\Private", "timestamp": "2026-11-22T00:00:00Z",
                                               "message": {"content": "I prefer the secret plan for Private."}}) + "\n", encoding="utf-8")
    monkeypatch.setitem(lh.PATHS, "chrome", lambda: [chrome])
    monkeypatch.setitem(lh.PATHS, "powershell", lambda: [ps])
    monkeypatch.setitem(lh.PATHS, "claude", lambda: [claude])
    monkeypatch.setattr(lh, "MIN_VISITS", 2)
    monkeypatch.setattr(lh, "MIN_RUNS", 2)
    return {"chrome": chrome, "powershell": ps, "claude": claude}


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("laptop history must not touch the network")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    from nova.brain import llm
    for name in ("chat", "chat_stream", "generate", "cloud_chat"):
        if hasattr(llm, name):
            monkeypatch.setattr(llm, name, refuse)


def _texts(src=None):
    return {f["key"]: f["text"] for f in lh.facts(src)}


def test_everything_is_off_until_switched_on(sources, no_network, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("an off source was read")
    for src in lh.SOURCES:
        monkeypatch.setitem(lh.READERS, src, boom)
    assert lh.load()["on"] == {"chrome": False, "powershell": False, "claude": False}
    assert not lh.due()
    assert lh.run() == {}
    assert lh.facts() == []


def test_chrome_sites_and_searches_become_unconfirmed_facts(sources, no_network):
    lh.set_source("chrome", True)
    lh.never_add("https://secret-bank.example/whatever")
    out = lh.run()
    assert out["chrome"]["read"] == 12
    t = _texts("chrome")
    assert t["site:github.com"] == "You often visit github.com (3 visits)."
    assert "topic:flask" in t                                    # two searches about flask
    assert not any("secret-bank" in k for k in t)                # never-listed site skipped
    assert not any(k.startswith("site:google") for k in t)       # a results page is a search, not a visit
    facts = lh.facts("chrome")
    assert all(f["kind"] == "history" and f["source"] == "chrome" and f["confirmed"] is False for f in facts)
    # the temp copy is gone, and the cursor makes the next read incremental
    assert lh.run(now=lh.load()["last_run"] + lh.READ_EVERY + 1)["chrome"]["read"] == 0
    _add_visits(sources["chrome"], ["https://github.com/d"], 1_790_000_500)
    assert lh.run()["chrome"]["read"] == 1
    assert _texts("chrome")["site:github.com"] == "You often visit github.com (4 visits)."


def test_secret_looking_lines_never_reach_facts_or_counts(sources, no_network):
    for src in lh.SOURCES:
        lh.set_source(src, True)
    lh.run()
    import nova.store as store
    kept = json.dumps(learner.load_facts()) + store.current().file("laptop_history.json").read_text(encoding="utf-8")
    for bad in ("hunter2", "482913", "otp", "sk-abc", "Bearer", "mysql", "curl", "$env"):
        assert bad.lower() not in kept.lower(), bad
    t = _texts("powershell")
    assert t["program:git"] == "In PowerShell you often run git (3 times)."
    assert t["program:python"] == "In PowerShell you often run python (3 times)."


def test_claude_transcripts_give_projects_and_preferences(sources, no_network):
    lh.set_source("claude", True)
    lh.never_add("C:\\Work\\Private")
    lh.run()
    t = _texts("claude")
    assert t["project:laptop assistant"].startswith('You work on "Laptop assistant" with Claude Code (1 session, last 21 Nov 2026')
    said = [v for k, v in t.items() if k.startswith("said:")]
    assert said == ['You told Claude Code (in "Laptop assistant"): "I prefer small PRs with one change each."']
    assert not any("Private" in v or "secret plan" in v for v in t.values())     # never-listed folder skipped
    assert not any("tool output" in v or "meta" in v for v in t.values())         # only the user's own words


def test_never_list_also_skips_powershell_lines_in_that_folder(sources, no_network):
    lh.set_source("powershell", True)
    lh.never_add("C:/Work/Private")
    lh.run()
    assert "program:cd" not in _texts("powershell")


def test_delete_keeps_a_fact_gone_and_forget_clears_a_source(sources, no_network):
    lh.set_source("chrome", True)
    lh.set_source("powershell", True)
    lh.run()
    gh = next(f for f in lh.facts("chrome") if f["key"] == "site:github.com")
    assert lh.delete(gh["id"]) == 1
    _add_visits(sources["chrome"], ["https://github.com/z"], 1_790_000_900)
    lh.run(now=lh.load()["last_run"] + lh.READ_EVERY + 1)
    assert "site:github.com" not in _texts("chrome")
    n = lh.forget("chrome")
    assert n > 0 and lh.facts("chrome") == [] and "chrome" not in lh.load()["tally"]
    assert lh.facts("powershell")                                  # other sources untouched
    # forgetting keeps the read position: old history is not read again
    assert lh.run(now=lh.load()["last_run"] + lh.READ_EVERY + 1)["chrome"]["read"] == 0


def test_pause_and_switch_off_read_nothing(sources, no_network):
    lh.set_source("chrome", True)
    lh.set_paused(True)
    assert lh.run() == {} and lh.facts() == []
    lh.set_paused(False)
    lh.set_source("chrome", False)
    assert lh.run() == {} and lh.facts() == []


def test_history_facts_have_their_own_section_on_the_knowledge_page(sources, no_network, client):
    lh.set_source("powershell", True)
    lh.run()
    assert all(f["text"].startswith("In PowerShell") is False for f in client.get("/api/knowledge").get_json()["facts"])
    h = client.get("/api/laptop_history").get_json()
    ps = next(s for s in h["sources"] if s["id"] == "powershell")
    assert ps["on"] and ps["found"] and ps["what"] and ps["facts"] and not ps["facts"][0]["confirmed"]
    fid = ps["facts"][0]["id"]
    assert client.post("/api/laptop_history", json={"do": "fact_confirm", "id": fid}).get_json()["message"] == "Marked as right."
    assert client.post("/api/laptop_history", json={"do": "fact_delete", "id": fid}).get_json()["ok"]
    assert client.post("/api/laptop_history", json={"do": "source_on", "source": "nope"}).status_code == 400
    assert client.post("/api/laptop_history", json={"do": "never_add", "value": "example.com"}).get_json()["ok"]
    assert "example.com" in client.get("/api/laptop_history").get_json()["never"]
    assert client.post("/api/laptop_history", json={"do": "forget", "source": "powershell"}).get_json()["ok"]
    assert lh.facts("powershell") == []


def test_research_site_facts_still_work_beside_history_facts(sources, no_network):
    lh.set_source("chrome", True)
    lh.run()
    learner.learn_sites("flask tutorials", ["https://a.com/1"], ["https://a.com/1"])
    assert learner.sites_for("flask tutorials")[0] == ["a.com"]
    assert lh.facts("chrome")
