"""Research and apps the way a person does it: Google in Chrome, reading the page, checking an app is installed."""
from nova import store
from nova.actions import coworker, skills, tools
from nova.brain import toolpick
from nova.shared import ROOT


def _real_skills(monkeypatch):
    monkeypatch.setattr(store.current(), "skills_dir", ROOT / "skills")       # the skill files that ship with Nova
    skills.library._cache.update(key=None, skills=[])


def test_research_tools_registered_and_safe():
    names = {t["name"] for t in tools.TOOLS}
    assert {"web_research", "read_page", "find_app"} <= names
    assert not {"web_research", "read_page", "find_app"} & tools.RISKY      # reading/searching needs no OK
    assert "install_app" in tools.RISKY                                     # installing still asks first


def test_web_research_googles_in_chrome(monkeypatch):
    opened = []
    monkeypatch.setattr(tools.apps, "_chrome_exe", lambda: r"C:\Chrome\chrome.exe")
    monkeypatch.setattr(tools.subprocess, "Popen", lambda args: opened.append(args))
    monkeypatch.setattr(tools.apps, "_wait_for_window", lambda words, secs=8.0: None)
    out = tools.web_research("fastapi websockets", site="github")
    assert opened == [[r"C:\Chrome\chrome.exe",
                       "https://www.google.com/search?q=fastapi+websockets+site%3Agithub.com"]]
    assert "Chrome" in out and "read_page" in out


def test_web_research_ignores_odd_sites(monkeypatch):
    opened = []
    monkeypatch.setattr(tools.apps, "_chrome_exe", lambda: "chrome.exe")
    monkeypatch.setattr(tools.subprocess, "Popen", lambda args: opened.append(args))
    monkeypatch.setattr(tools.apps, "_wait_for_window", lambda words, secs=8.0: None)
    tools.web_research("cats", site="x & del *")
    assert opened[0][1].endswith("q=cats")


def test_find_app_missing_asks_before_installing(monkeypatch):
    monkeypatch.setattr(tools.system, "search_installable_apps", lambda q: "Name  Id\nZoom  Zoom.Zoom  6.0  winget")
    monkeypatch.setattr(tools.apps, "_start_menu_match", lambda n: None)
    monkeypatch.setattr(tools.apps, "_installed_names", lambda n: [])
    out = tools.find_app("zzz-not-an-app")
    assert "NOT installed" in out and "Zoom.Zoom" in out and "ask" in out.lower()


def test_find_app_finds_windows_basics():
    assert "is installed" in tools.find_app("notepad", search_store=False)


def test_research_skills_run_in_the_background(monkeypatch):
    _real_skills(monkeypatch)
    for name in ("web-research", "code-research"):
        s = skills.get(name)
        assert s and s["kind"] == "routine" and s["tools"] == ["research_in_background"]
    assert skills.get("code-research")["steps"][0]["args"]["kind"] == "code"
    assert "research_in_background" not in tools.RISKY        # reading the web in the background needs no OK


def test_result_links_keep_real_sites_once():
    from nova.actions import coworker
    links = [("Images", "https://www.google.com/imghp"),
             ("Python's asyncio: A Hands-On Walkthrough Real Python https://realpython.com", "https://realpython.com/async-io-python/"),
             ("Another Real Python page about asyncio", "https://realpython.com/other/"),
             ("Asyncio in Python - Full Tutorial YouTube", "https://www.youtube.com/watch?v=x"),
             ("Ad: buy a course now please", "https://www.google.com/aclk?sa=l"),
             ("asyncio - Asynchronous I/O - Python docs", "https://docs.python.org/3/library/asyncio.html")]
    assert coworker.result_links(links) == [
        ("Python's asyncio: A Hands-On Walkthrough Real Python", "https://realpython.com/async-io-python/"),
        ("asyncio - Asynchronous I/O - Python docs", "https://docs.python.org/3/library/asyncio.html")]


def test_research_report_parsing():
    from nova.actions import coworker
    assert coworker._parse_report("Headline: Use asyncio.run.\n\n1. Point [1]") == ("Use asyncio.run.", "1. Point [1]")
    assert coworker._parse_report("Use TaskGroup. More text.")[0] == "Use TaskGroup."


def test_research_requests_get_the_research_tools(monkeypatch):
    _real_skills(monkeypatch)
    assert skills.match("do some research on the best python web framework")["name"] == "web-research"
    assert skills.match("help me research code for parsing pdfs")["name"] == "code-research"
    picked = toolpick.pick("look up the docs for pandas groupby on the web")
    assert "research_in_background" in picked


def test_unwrap_search_engine_links():
    from nova.actions import coworker
    assert coworker.unwrap("https://duckduckgo.com/l/?uddg=https%3A%2F%2Frealpython.com%2Fasync%2F&rut=x") == \
        "https://realpython.com/async/"
    assert coworker.unwrap("https://www.google.com/url?q=https://docs.python.org/3/&sa=U") == "https://docs.python.org/3/"
    assert coworker.unwrap("https://www.bing.com/ck/a?!&&p=x&u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9wYWdl&ntb=1") == \
        "https://example.com/page"
    assert coworker.unwrap("https://example.com/") == "https://example.com/"


def test_search_skips_an_engine_that_wants_a_robot_check(monkeypatch):
    from nova.actions import coworker
    monkeypatch.setattr(coworker, "_blocked", {})
    pages = {"google": [("Why did this happen?", "https://www.google.com/sorry/index?continue=x")],
             "duckduckgo": [("A good article about the topic", "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fa.com%2Fx")]}
    opened, closed = [], []
    monkeypatch.setattr(coworker, "_open_parked", lambda url, *a, **k: opened.append(url) or url)
    monkeypatch.setattr(coworker, "close_window", lambda h: closed.append(h))
    monkeypatch.setattr(coworker.tools, "page_links", lambda h: next(v for k, v in pages.items() if k in h))
    monkeypatch.setattr(coworker.tools, "page_text", lambda h, n=0: "")
    monkeypatch.setattr(coworker.time, "sleep", lambda s: None)
    links = coworker.search("topic", "chrome.exe", "chrome", [])
    assert links == [("A good article about the topic", "https://a.com/x")]
    assert "google" in opened[0] and "duckduckgo" in opened[1] and len(closed) == 2
    assert "Google" in coworker._blocked                     # left alone for a while, never solved


def test_research_job_saves_report_without_opening_windows(monkeypatch, tmp_path):
    from nova.actions import coworker
    done = []
    monkeypatch.setattr(coworker.tools, "HOME", tmp_path)
    monkeypatch.setattr(coworker, "_browser", lambda: ("chrome.exe", "chrome"))
    monkeypatch.setattr(coworker, "search", lambda q, p, e, w: [("Asyncio guide for everyone", "https://a.com/x")])
    monkeypatch.setattr(coworker, "_open_parked", lambda url, *a, **k: 1)
    monkeypatch.setattr(coworker, "close_window", lambda h: None)
    monkeypatch.setattr(coworker, "_title", lambda h: "Asyncio guide - Google Chrome")
    monkeypatch.setattr(coworker.tools, "page_text", lambda h, n=0: "asyncio lets you run coroutines. " * 20)
    monkeypatch.setattr(coworker, "open_hidden", lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened")))
    coworker.configure(write=lambda p, n: "Headline: Use asyncio.run to start.\n\n1. Use asyncio.run at the top [1]",
                       on_done=done.append)
    job, msg = coworker.research("asyncio")
    for _ in range(100):
        if done:
            break
        __import__("time").sleep(0.05)
    assert done and done[0]["status"] == "done", done and done[0]["error"]
    report = (tmp_path / "Documents" / "Nova" / "Research - asyncio.txt").read_text(encoding="utf-8")
    assert "Use asyncio.run to start." in report and "https://a.com/x" in report
    assert done[0]["hwnd"] is None and "1 source." in done[0]["result"]
    coworker.configure()


# ---- the user pulling a background window to the front (it used to be pushed straight back off-screen)
import pytest as _pytest  # noqa: E402


@_pytest.fixture
def window(monkeypatch):
    st = {"front": 0, "fronted": [], "moved": [], "closed": [], "now": 1000.0}
    monkeypatch.setattr(coworker, "claimed", set())
    monkeypatch.setattr(coworker, "_born", {})
    monkeypatch.setattr(coworker, "_mode", {})
    monkeypatch.setattr(coworker, "WATCH", False)
    st["iconic"] = False
    monkeypatch.setattr(coworker._user32, "IsIconic", lambda h: st["iconic"])
    monkeypatch.setattr(coworker.time, "time", lambda: st["now"])
    monkeypatch.setattr(coworker._user32, "IsWindow", lambda h: True)
    monkeypatch.setattr(coworker._user32, "GetForegroundWindow", lambda: st["front"])
    monkeypatch.setattr(coworker._user32, "PostMessageW", lambda h, m, w, l: st["closed"].append(h))
    monkeypatch.setattr(coworker, "_bring_on_screen", lambda h: st["moved"].append(h))
    monkeypatch.setattr(coworker, "_note", lambda t: None)
    monkeypatch.setattr(coworker.tools, "_focus", lambda h: st["fronted"].append(h))
    return st


def test_chrome_grabbing_the_front_on_startup_is_not_the_user(window):
    window["front"] = 7
    assert coworker.user_took(7) is False and 7 not in coworker.claimed      # first seen: inside the grace period
    window["now"] += coworker.CLAIM_GRACE - 1
    assert coworker.user_took(7) is False


def test_pulling_the_window_forward_claims_it_and_it_stays(window):
    assert coworker.user_took(7) is False                                    # starts its life hidden
    window["now"] += coworker.CLAIM_GRACE + 1
    window["front"] = 7                                                      # the user clicks it in the taskbar
    assert coworker.user_took(7) is True and window["moved"] == [7]
    coworker.park(7, front=99)                                               # the research loop tries to hide it again
    coworker.keep_hidden(7, front=99)
    assert window["fronted"] == [] and window["moved"] == [7]                # ...and does nothing: no re-parking, no focus theft
    coworker.close_window(7)                                                 # the job ends: it is left open
    assert window["closed"] == []


def test_a_window_nobody_touched_is_still_hidden_and_closed(window):
    coworker.user_took(7)
    window["now"] += coworker.CLAIM_GRACE + 1
    window["front"] = 99                                                     # the user is working in another app
    assert coworker.user_took(7) is False
    coworker.close_window(7)
    assert window["closed"] == [7]


def test_user_took_forgets_closed_windows(window, monkeypatch):
    coworker.claimed.add(7)
    monkeypatch.setattr(coworker._user32, "IsWindow", lambda h: False)
    assert coworker.user_took(7) is False and 7 not in coworker.claimed


def test_minimizing_it_hands_it_back_to_the_job(window, monkeypatch):
    parked = []
    coworker._track(7, "park")
    window["now"] += coworker.CLAIM_GRACE + 1
    window["front"] = 7
    assert coworker.user_took(7) is True                                     # pulled up: the user's
    monkeypatch.setattr(coworker, "park", lambda h, front=None: parked.append(h))
    window["front"], window["iconic"] = 99, True                             # ...then minimized: put back
    assert coworker.user_took(7) is False and 7 not in coworker.claimed
    assert parked == [7]                                                     # parked off-screen again, still readable
    window["iconic"] = False
    coworker.close_window(7)                                                 # and closed at the end as usual
    assert window["closed"] == [7] and 7 not in coworker._born


def test_minimized_style_jobs_stay_minimized_when_handed_back(window, monkeypatch):
    parked = []
    coworker._track(8, "min")                                                # e.g. the hidden Gmail draft window
    window["now"] += coworker.CLAIM_GRACE + 1
    window["front"] = 8
    assert coworker.user_took(8)
    monkeypatch.setattr(coworker, "park", lambda h, front=None: parked.append(h))
    window["front"], window["iconic"] = 99, True
    assert coworker.user_took(8) is False and parked == []


def test_a_window_still_open_when_the_job_ends_becomes_the_users(window):
    coworker._track(7, "park")
    window["now"] += coworker.CLAIM_GRACE + 1
    window["front"] = 7
    coworker.user_took(7)
    coworker.close_window(7)
    assert window["closed"] == [] and 7 not in coworker._born                # left open, and no longer followed
