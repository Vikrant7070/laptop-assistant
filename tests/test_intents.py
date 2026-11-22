"""R14: the regexes that read the user's words live in nova/intents.py, nowhere else in the app package.

The golden cases for each intent are in tests/routing.jsonl (`"kind": "intent"`, run by test_routing_golden.py).
"""
import ast
from pathlib import Path

from nova import intents
from nova.core import app

APP = Path(app.__file__).parent

# re.compile in nova/core/app that is NOT about the user's words: it reads a model's answer, checks a name or
# splits sentences. A new user-intent regex goes in nova/intents.py instead of onto this list.
NOT_USER_INTENT = {
    ("body", "REACT_RULES"),                           # reactions to both sides of the chat, a table of its own
    ("conversation", "END"), ("conversation", "CLOSED_STAGE"), ("conversation", "OPEN_STAGE"),
    ("conversation", "DONE_CLAIM_RE"), ("conversation", "PROMISE_RE"), ("conversation", "BODY_CLAIM_RE"),
    ("integrations", "SECRETY"),                       # a phone notification's text, not the user
    ("learning", "FAILED_RE"),                         # her own final reply
    ("session", "END"),
    ("skillrun", "FAIL_RE"),                           # a tool's result
}


def _compiled_names(path):
    """Names assigned from an expression that calls re.compile, at any depth."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Assign):
            continue
        calls = [n for n in ast.walk(node.value) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "compile" and isinstance(n.func.value, ast.Name) and n.func.value.id == "re"]
        if calls:
            found |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return found


def test_no_user_intent_regex_outside_intents():
    seen = {(p.stem, name) for p in APP.glob("*.py") for name in _compiled_names(p)}
    assert seen - NOT_USER_INTENT == set(), "user-intent regex outside nova/intents.py"
    assert NOT_USER_INTENT - seen == set(), "stale entry on NOT_USER_INTENT: remove it"


def test_stop_words_follow_the_character_name():
    rx = intents.just_stop_re("(?:lucy)")
    assert rx.match("lucy stop") and rx.match("ruko") and not rx.match("lucy stop the music")


def test_table_intents_pick_the_first_action():
    assert intents.match("video", "exit full screen") == "exit_fullscreen"     # also says "full screen"
    assert intents.match("fact", "") is None
