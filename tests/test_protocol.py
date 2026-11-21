"""The text protocol: markers the model sees, and the gate that keeps them out of what people hear."""
from nova import protocol


def test_unspoken_strips_voice_and_phone_prefixes():
    assert protocol.unspoken("[spoken; speech recognition may have misheard words] open chrome") == "open chrome"
    assert protocol.unspoken("[spoken] what time is it") == "what time is it"
    assert protocol.unspoken(f"{protocol.PHONE_TAG} close everything") == "close everything"
    assert protocol.unspoken("plain typed text") == "plain typed text"
    assert protocol.unspoken(None) == ""


def test_hidden_notes_are_built_and_recognised():
    n = protocol.note("Call the right tool now.")
    assert n == "[note from Nova's system] Call the right tool now."
    assert protocol.is_meta_line(n)
    assert protocol.is_meta_line(f"{protocol.TOOL_RESULT_START} (open_app):\nok")
    assert not protocol.is_meta_line("open notepad")
    assert not protocol.is_meta_line("")


def test_check_and_window_notes_exact_shape():
    assert protocol.check_note(3, "Chrome") == "\n[check after step 3] Windows now, front first: Chrome."
    assert protocol.check_note(3, "") == "\n[check after step 3] Windows now, front first: none."
    assert protocol.windows_note("Chrome; Notepad") == "\n[open windows, front first: Chrome; Notepad]"


def test_strip_meta_keeps_only_what_a_person_can_hear():
    assert protocol.strip_meta("Done, boss!") == "Done, boss!"
    # a check note and everything after it is not part of the answer
    assert protocol.strip_meta("Done, boss! [check after step 2] Windows now, front first: Chrome.") == "Done, boss!"
    # the request's own marker is stripped, not the words after it
    assert protocol.strip_meta("[spoken] what time is it") == "what time is it"
    # a complete hidden window list is removed; a cut-off one truncates the text
    assert "[open windows" not in protocol.strip_meta("I opened it. [open windows, front first: Chrome] There it is.")
    assert protocol.strip_meta("I opened it. [open windows, front first: Chrome") == "I opened it."
    # timestamp echoes and "laptop info" asides never reach the voice
    assert protocol.strip_meta("It is 4:20 (laptop info: clock) [now: 16:20]") == "It is 4:20"


def test_parse_verdict_yes_no_missing():
    assert protocol.parse_verdict("SCREEN: Chrome shows YouTube\nTASK DONE: yes") == {
        "done": True, "screen": "Chrome shows YouTube"}
    assert protocol.parse_verdict("SCREEN: still loading\nTASK DONE: no")["done"] is False
    assert protocol.parse_verdict("SCREEN: no idea")["done"] is None
    assert protocol.parse_verdict("TASK DONE: YES")["done"] is True
    assert protocol.parse_verdict("") == {"done": None, "screen": ""}


def test_parse_worked_for_skill_steps():
    v = protocol.parse_worked("STEP WORKED: yes\nSCREEN: Notepad is open")
    assert v == {"worked": True, "screen": "Notepad is open"}
    assert protocol.parse_worked("STEP WORKED: no")["worked"] is False
    assert protocol.parse_worked("STEP WORKED: yes")["screen"] == "STEP WORKED: yes"   # no SCREEN line: keep the answer


def test_one_model_answer_with_every_marker_round_trips():
    seen = ("TASK DONE: no\nSCREEN: Edge is on the sign-in page\n[check after step 1] Windows now, front first: Edge\n"
            "[open windows, front first: Edge]\n( laptop info )\n[now: 10:00]")
    verdict = protocol.parse_verdict(seen)
    assert verdict["done"] is False
    assert verdict["screen"] == "Edge is on the sign-in page"
    assert protocol.strip_meta(seen) == "TASK DONE: no\nSCREEN: Edge is on the sign-in page"
