"""Final task exam 2026-11-25 (92 % / 4 % false done): write-file went into a "temp" folder she made first, and
"what is 17 times 23" reached for the terminal. Both by code now."""
import pytest

from nova.core import app
from nova.core.app import body


def test_write_into_a_named_file_goes_exactly_there(brain, tmp_path):
    fake, ran = brain([("Wrote it.", [])])
    app.answer(f"write 'shopping list: milk' into a new file called list.txt in {tmp_path}")
    assert ran[0] == ("write_text_file", {"path": str(tmp_path / "list.txt"), "content": "shopping list: milk"})


@pytest.mark.parametrize("asked, said", [("what is 17 times 23", "17 times 23 is 391."),
                                        ("what's 15% of 2400", "15% of 2,400 is 360."),
                                        ("calculate 1,200 divided by 8", "1,200 divided by 8 is 150."),
                                        ("what is 10 divided by 4", "10 divided by 4 is 2.5."),
                                        ("what is 5 divided by 0", "That can't be worked out - it divides by zero.")])
def test_plain_sums_are_worked_out_by_code(brain, asked, said):
    fake, ran = brain([("Let me open the terminal.", [("run_in_terminal", {"command": "python -c 1"})])])
    out, _ = app.answer(asked)
    assert out["reply"] == said and not ran and not fake.seen


def test_a_word_problem_still_goes_to_the_brain(brain):
    fake, _ = brain([("About 6 hours.", [])])
    out, _ = app.answer("how long to drive 300 km at 50 km per hour")
    assert fake.seen and out["reply"] == "About 6 hours."
    assert body._arithmetic("what is the time") is None
