"""Tools on the running task's card (manager plan 4d): questions don't stop the work.

    ask_user(question, key, why, needed_now)  answered from what is known (the card, then the user's saved answers)
                                              when it can be; otherwise it waits on the card while she carries on
                                              with the steps that don't need it, and code asks everything on one
                                              card when she has nothing else to do (nova/core/app/watching.gather)
    note_fact(key, value, guess)              something she found (or guessed - marked as one) goes on the card

Both change nothing on the laptop. The card is reached through known.current_card (set by the app).
"""
from nova import known
from nova.actions.registry import tool

YES = (True, 1, "1", "true", "True", "yes")


def _card():
    return known.current_card() if known.current_card else None


# Core, so it is in every local prompt: the description is kept short (the how is in the tool's reply, not here).
@tool("Ask the user something the task needs, instead of asking in your reply. Then carry on with other steps.",
      friendly="Asked you something", core=True,
      props={"question": {"type": "string"},
             "key": {"type": "string", "description": "e.g. github_username"},
             "why": {"type": "string"},
             "needed_now": {"type": "boolean", "description": "nothing else can be done without it"}},
      required=["question", "key"])
def ask_user(question, key, why="", needed_now=False):
    card = _card()
    if card is None:
        return "There's no task running - ask the user in your reply."
    hit, source = known.lookup(card, key), "the task card"
    if hit is None:
        from nova.actions import manager
        saved = manager.recall(known.key_of(key))
        if saved:
            hit, source = known.add(card, key, saved, "found"), "what the user told me before"
    if hit:
        return f"Already known - don't ask the user: {hit['key']} = {hit['value']} (from {source})."
    known.queue(card, key, question, why, needed_now in YES)
    if needed_now in YES:
        return ("Queued for the user. Nothing else can go on without it, so end your turn now with no other words: "
                "I'll ask the user and you'll carry on with the answer.")
    return ("Queued for the user - NOT asked yet. Carry on now with the steps that don't need this answer. When "
            "nothing else can be done without it, end your turn: I'll ask the user everything at once.")


@tool("Undo a change Nova made that can be undone (moved or renamed files, a new folder or file, a file's text, dark "
      "mode, brightness, volume). which: 'latest', a change number, or words from it.",
      friendly="Undid a change", hints="undo that, put it back, change it back, revert that, undo the last change",
      props={"which": {"type": "string", "description": "'latest', a number, or words"}}, required=[])
def undo_last_change(which="latest"):
    from nova.actions import undo
    return undo.undo(which or "latest")


@tool("Note a fact for this task on its card - something you found out on the way, or a guess (guess=true) - so "
      "later steps and questions use it.",
      friendly="Noted it on the task card", hints="note this fact for the task, remember this for now, found out",
      group=("ask_user",),
      props={"key": {"type": "string", "description": "a short name, e.g. repo_url"},
             "value": {"type": "string"},
             "guess": {"type": "boolean", "description": "true when it is only your guess"}},
      required=["key", "value"])
def note_fact(key, value, guess=False):
    card = _card()
    if card is None:
        return "There's no task running - nothing to note it on."
    entry = known.add(card, key, value, "guess" if guess in YES else "found")
    if entry is None:
        return "Not noted: give a short key and a value."
    return f"Noted on the task card: {entry['key']} = {entry['value']}" + (" (a guess)" if entry["how"] == "guess" else "") + "."
