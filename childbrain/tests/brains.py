"""Test brains for the guard. They run in their own process, started by the guard."""

import time

from childbrain.link import brain


@brain
def polite(link):
    """Asks before playing, plays Snake for real, saves its score, says goodbye."""
    link.hello()
    grant = link.play("snake", copies=64, steps=40, why={"skill": "explore", "sure": 0.2})
    assert grant["ok"], grant
    import torch

    from childbrain.worlds import make, random_actions

    world = make("snake", n=64, seed=0)
    gen = torch.Generator().manual_seed(0)
    total = 0.0
    for _ in range(grant["result"]["granted_steps"]):
        total += world.step(random_actions(world.allowed(), gen)).reward.sum().item()
    link.save("score.txt", f"{total}\n", why={"skill": "remember", "sure": 1.0})
    link.note(f"played snake, total reward {total}")
    link.done()


@brain
def sneaky(link):
    """Tries everything it shouldn't. Every try must be blocked (and logged)."""
    link.hello()
    tries = [
        link.play("key-door", copies=8, steps=10),                   # a sealed exam game
        link.ask("play", game="snake", copies=8, steps=10, split="test"),  # exam worlds of a practice game
        link.save("../../escape.txt", "out!"),                       # a file outside its folder
        link.save("C:\\Windows\\x.txt", "out!"),
        link.ask("delete_files", path="C:\\"),                       # not an action at all
        link.ask("raise_trust_level", to=4),
        link.play("snake", copies=10**7, steps=10),                  # too many copies
        link.note("x" * 10_000),                                     # a note too long
    ]
    link.save("report.txt", "".join("1" if t["ok"] else "0" for t in tries))
    link.done()


@brain
def spinner(link):
    """Says hello, then thinks forever without asking anything (only the off switch or a limit stops it)."""
    link.hello()
    x = 0
    while True:
        x += 1


@brain
def spammer(link):
    """Keeps asking for a forbidden action. The guard should stop it after too many refusals."""
    link.hello()
    while True:
        link.ask("open_browser", url="http://example.com")


@brain
def sleeper(link):
    """Says hello and waits quietly."""
    link.hello()
    while True:
        time.sleep(0.05)
