"""👀 'Watch me': recorded events -> skill steps (recorder.events_to_trace + skills.steps_from_trace).

No real hooks here (conftest turns them off) - the converter is fed synthetic events, the shape the worker emits."""
from nova.actions import recorder, skills

CHROME = "chrome.exe"


def ev(kind, t, **kw):
    return {"kind": kind, "t": float(t), **kw}


def tools_of(trace):
    return [(x["tool"], x["args"]) for x in trace]


def test_app_name_keeps_the_stable_part_of_a_title():
    assert recorder.app_name("lofi hip hop radio - YouTube - Google Chrome", CHROME) == "YouTube"
    assert recorder.app_name("Inbox (3) - me@example.com - Gmail - Google Chrome", CHROME) == "Gmail"   # no e-mail address
    assert recorder.app_name("notes.txt - Notepad", "notepad.exe") == "Notepad"
    assert recorder.app_name("app.py - Laptop assistant - Visual Studio Code", "Code.exe") == "Visual Studio Code"
    assert recorder.app_name("", "Spotify.exe") == "Spotify"


def test_a_clicked_link_becomes_open_website_of_the_new_page():
    t = recorder.events_to_trace([
        ev("click", 1, name="Simulcasts", ctype="Hyperlink", browser=True, window="Crunchyroll - Google Chrome", proc=CHROME),
        ev("url", 2, url="https://www.crunchyroll.com/simulcasts", window="Simulcasts - Crunchyroll - Google Chrome", proc=CHROME),
    ])
    assert tools_of(t) == [("open_website", {"url_or_search": "https://www.crunchyroll.com/simulcasts"})]


def test_a_button_that_changes_the_page_stays_a_click():
    t = recorder.events_to_trace([
        ev("click", 1, name="New episodes", ctype="Button", browser=True, window="Crunchyroll - Google Chrome", proc=CHROME),
        ev("url", 2, url="https://www.crunchyroll.com/new", window="Crunchyroll - Google Chrome", proc=CHROME),
    ])
    assert tools_of(t) == [("click_element", {"name": "New episodes", "window": "Crunchyroll"})]


def test_typing_in_the_address_bar_and_enter_becomes_open_website():
    t = recorder.events_to_trace([
        ev("click", 1, name="Address and search bar", ctype="Edit", address_bar=True, window="New Tab - Google Chrome", proc=CHROME),
        ev("typed", 3, box="Address and search bar", ctype="Edit", address_bar=True, value=None, window="New Tab - Google Chrome", proc=CHROME),
        ev("key", 3.1, combo="enter", address_bar=True, window="New Tab - Google Chrome", proc=CHROME),
        ev("url", 4, url="netflix.com/browse", window="Netflix - Google Chrome", proc=CHROME),
    ])
    assert tools_of(t) == [("open_website", {"url_or_search": "netflix.com/browse"})]


def test_click_a_box_and_type_becomes_one_fill_in():
    t = recorder.events_to_trace([
        ev("click", 1, name="Search", ctype="Edit", window="YouTube - Google Chrome", proc=CHROME),
        ev("typed", 4, box="Search", ctype="Edit", value="lofi beats", window="YouTube - Google Chrome", proc=CHROME),
        ev("key", 4.2, combo="enter", window="YouTube - Google Chrome", proc=CHROME),
        ev("url", 5, url="https://www.youtube.com/results?search_query=lofi+beats", window="YouTube - Google Chrome", proc=CHROME),
    ])
    assert tools_of(t) == [("fill_in", {"text": "lofi beats", "box": "Search", "window": "YouTube", "press_enter": True})]


def test_passwords_and_codes_never_appear_only_sign_in_steps():
    t = recorder.events_to_trace([
        ev("click", 1, name="Email", ctype="Edit", window="Log in - Crunchyroll - Google Chrome", proc=CHROME),
        ev("typed", 2, box="Email", ctype="Edit", value=None, secret=True, window="Log in - Crunchyroll - Google Chrome", proc=CHROME),
        ev("click", 3, name="Password", ctype="Edit", password=True, window="Log in - Crunchyroll - Google Chrome", proc=CHROME),
        ev("login", 4, box="Password", window="Log in - Crunchyroll - Google Chrome", proc=CHROME),
        ev("key", 4.1, combo="enter", window="Log in - Crunchyroll - Google Chrome", proc=CHROME),
        ev("code", 8, box="Verification code", window="Verify - Crunchyroll - Google Chrome", proc=CHROME),
    ])
    assert tools_of(t) == [("login_if_needed", {"window": "Crunchyroll"}), ("wait_for_code", {"window": "Crunchyroll"})]
    assert "hunter" not in str(t)


def test_a_new_app_is_opened_a_known_one_focused():
    t = recorder.events_to_trace([
        ev("click", 0.5, name="Start", ctype="Button", window="Taskbar", proc="explorer.exe", shell=True),   # the taskbar: left out
        ev("focus", 1, window="Spotify Premium", proc="Spotify.exe", new_app=True),
        ev("focus", 5, window="notes.txt - Notepad", proc="notepad.exe", new_app=False),
        ev("key", 6, combo="ctrl+s", window="notes.txt - Notepad", proc="notepad.exe"),
        ev("focus", 9, window="Slack", proc="slack.exe", new_app=False),
        ev("focus", 9.4, window="Mail", proc="mail.exe", new_app=False),                              # passed through
    ])
    assert tools_of(t) == [("open_app", {"name": "Spotify Premium"}), ("press_keys", {"keys": "ctrl+s"}),
                           ("window_action", {"title_or_app": "Mail", "action": "focus"})]


def test_shortcut_keys_right_and_double_clicks():
    t = recorder.events_to_trace([
        ev("key", 1, combo="f", window="Crunchyroll - Google Chrome", proc=CHROME),
        ev("click", 2, name="report.pdf", ctype="ListItem", button="right", window="Downloads", proc="explorer.exe"),   # File Explorer: kept
        ev("click", 3, name="Photos", ctype="ListItem", double=True, window="Pictures", proc="explorer.exe"),
        ev("key", 4, combo="ctrl+alt+w", window="x", proc="x.exe"),                                  # Nova's own hotkey
    ])
    assert tools_of(t) == [("press_keys", {"keys": "f"}),
                           ("click_element", {"name": "report.pdf", "window": "Downloads", "button": "right"}),
                           ("click_element", {"name": "Photos", "window": "Pictures", "double": True})]


def test_nova_windows_and_unnamed_clicks():
    t = recorder.events_to_trace([
        ev("click", 1, name="Send", ctype="Button", window="Nova", proc="pythonw.exe", nova=True),
        ev("click", 2, name="", ctype="Pane", x=10, y=20, window="Game", proc="game.exe"),
    ])
    assert tools_of(t) == [("click_at", {"x": 10, "y": 20})]
    steps, _ = skills.steps_from_trace(t, "click the thing")
    assert "brain" in steps[0] and steps[0]["tools"] == ["list_clickable", "click_element"]   # coordinates never saved


def test_typing_that_cant_be_read_becomes_a_brain_step():
    t = recorder.events_to_trace([ev("typed", 1, box="Message", ctype="Custom", value=None, window="Teams", proc="ms-teams.exe")])
    steps, _ = skills.steps_from_trace(t, "write a message")
    assert "brain" in steps[0] and "Message" in steps[0]["brain"] and "fill_in" in steps[0]["tools"]
    assert skills.parse(skills.render_v2({"name": "x", "examples": ["x"]}, steps))["kind"] == "routine"


def test_redirects_and_browser_pages_are_not_extra_steps():
    t = recorder.events_to_trace([
        ev("url", 1, url="https://netflix.com", window="Netflix - Google Chrome", proc=CHROME),
        ev("url", 2, url="https://www.netflix.com/in/", window="Netflix - Google Chrome", proc=CHROME),   # redirect
        ev("url", 9, url="chrome://newtab/", window="New Tab - Google Chrome", proc=CHROME),
    ])
    assert tools_of(t) == [("open_website", {"url_or_search": "https://netflix.com"})]


def test_a_full_recording_makes_a_draft_skill():
    t = recorder.events_to_trace([
        ev("focus", 1, window="Google Chrome", proc=CHROME, new_app=True),
        ev("url", 2, url="https://www.crunchyroll.com", window="Crunchyroll - Google Chrome", proc=CHROME),
        ev("login", 5, box="Password", window="Crunchyroll - Google Chrome", proc=CHROME),
        ev("click", 9, name="Simulcasts", ctype="Hyperlink", browser=True, window="Crunchyroll - Google Chrome", proc=CHROME),
        ev("url", 10, url="https://www.crunchyroll.com/simulcasts", window="Crunchyroll - Google Chrome", proc=CHROME),
    ])
    steps, why = skills.steps_from_trace(t, "watch me")
    msg = skills.draft_from_steps("crunchy-new", "Opens Crunchyroll's new episodes", "new anime", steps, goal="watch me")
    assert msg.startswith("Saved a draft skill 'crunchy-new' with 4 steps")
    s = skills.get("crunchy-new")
    assert [x["tool"] for x in s["steps"]] == ["open_app", "open_website", "login_if_needed", "open_website"]
    assert s["status"] == "draft"


def test_listening_is_off_when_disabled():
    assert recorder.start(mode="listen") and not recorder.listening() and not recorder.user_acted_since(0)
    assert recorder.stop() == []


def test_key_names():
    assert recorder.key_name(0x46) == "f" and recorder.key_name(0x0D) == "enter" and recorder.key_name(0x72) == "f3"
    assert recorder.printable(0x41) and recorder.printable(0x20) and not recorder.printable(0x0D)
