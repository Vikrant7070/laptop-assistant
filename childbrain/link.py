"""The brain's side of the line to the guard. This is the brain's only way to act.

A brain is a function marked @brain that receives a GuardLink. Every action is a request; the guard
answers {"ok": True/False, "reason": ..., "result": ...}. `why` says which skill asked and how sure it
was, so the log can explain every action.
"""

from __future__ import annotations

import json
import os
from multiprocessing.connection import Client


def brain(fn):
    """Mark a function as a brain the guard may start."""
    fn.is_brain = True
    return fn


class GuardLink:
    def __init__(self):
        address = os.environ["CHILDBRAIN_GUARD_PIPE"]
        key = bytes.fromhex(os.environ.pop("CHILDBRAIN_GUARD_KEY"))  # gone from the environment once used
        self.conn = Client(address, family="AF_PIPE", authkey=key)

    def ask(self, action: str, why: dict | None = None, **args) -> dict:
        self.conn.send_bytes(json.dumps({"action": action, "args": args, "why": why or {}}).encode())
        return json.loads(self.conn.recv_bytes())

    def hello(self):
        return self.ask("hello")

    def play(self, game: str, copies: int, steps: int, change: str | None = None, why: dict | None = None):
        return self.ask("play", why, game=game, copies=copies, steps=steps, change=change)

    def save(self, name: str, text: str, why: dict | None = None):
        return self.ask("save", why, name=name, data=text)

    def note(self, text: str):
        return self.ask("log", text=text)

    def done(self):
        return self.ask("done")

    def close(self):
        self.conn.close()
