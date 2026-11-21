"""Skills (skills/*.md) as tools, and signing in for them."""


import re
import time

from nova.actions.registry import tool
from nova.actions.tools import common, forms, windows


@tool("Run one of your saved skills (a routine or recipe the user taught you) by its name.", friendly="Ran a skill",
      ask="Run a skill", hints="run my routine, do the usual, use your skill",
      props={"name": common.S, "input": {"type": "string", "description": "what it's for this time, if the skill needs it "
      "(e.g. the song or search words)"}}, required=["name"])
def run_skill(name, input=""):
    # app.execute runs skills itself (each step needs the approval rules and the screen settling); this is only
    # the fallback when called outside a conversation.
    from nova.actions import skills
    s = skills.get(name)
    if not s:
        return f"I have no skill called '{name}'. " + skills.describe()
    if s["kind"] == "recipe":
        return f"Follow your skill '{s['name']}' now, one tool call at a time:\n{s['body']}"
    return f"Skill '{s['name']}' can only run inside a conversation."


@tool("Save a NEW skill when the user teaches you a routine, e.g. 'when I say movie time, close "
      "everything and open Netflix'. Write steps one per line. Steps you can run exactly make a routine: "
      "'open_app name=Spotify', 'set_volume action=up, steps=5', 'say Enjoy!', 'wait 2' ('{input}' = what the "
      "user asks for each time). Plain-English steps make a recipe you follow (weaker - prefer steps you can "
      "run). To CHANGE a skill that exists, use edit_skill, not this.", friendly="Learned a new skill",
      hints="when I say this do that, learn this routine, teach you a new skill, remember how to do it, shortcut",
      props={"name": {"type": "string", "description": "short, like movie-time"},
      "description": {"type": "string", "description": "what it does and when to use it, in a sentence"},
      "steps": {"type": "string", "description": "one step per line"},
      "when": {"type": "string", "description": "phrases the user will say to start it, comma-separated"},
      "not_for": {"type": "string", "description": "similar requests that must NOT start it, comma-separated"},
      "kind": {"type": "string", "enum": ["routine", "recipe"]},
      "replace": {"type": "boolean", "description": "true only to overwrite a skill that already exists"}},
      required=["name", "description", "steps"])
def save_skill(name, description, steps, when="", kind="", replace=False, not_for="", requires_any=""):
    from nova.actions import skills
    return skills.save(name, description, steps, when, kind, replace=bool(replace), author="brain",
                       not_for=not_for, requires_any=requires_any)


@tool("Change a saved skill a little: add, change or remove ONE step, a check, or a trigger phrase. "
      "ops is a JSON list, e.g. [{\"op\":\"insert_step\",\"after\":\"s2\",\"step\":\"set_volume action=up, "
      "steps=5\"}] or [{\"op\":\"replace_step\",\"id\":\"s4\",\"step\":\"press_keys keys=f\",\"expect\":\"fullscreen\"}] "
      "or [{\"op\":\"delete_step\",\"id\":\"s3\"}] or [{\"op\":\"add_not_for\",\"text\":\"latest anime\"}]. "
      "Other ops: set_expect (id, expect), set_on_fail (id, on_fail), add_example (text), set_description (text). "
      "The old version is kept. Use this when the user corrects a skill - never retype the whole skill.",
      friendly="Changed a skill",
      hints="change my skill, fix the skill, update the skill, add a step to the skill, the skill should also, "
            "undo skill change",
      props={"name": common.S, "ops": {"type": "string", "description": "JSON list of changes (max 5)"}},
      required=["name", "ops"])
def edit_skill(name, ops):
    """Change one saved skill a little (add / change / remove a step, a check, a trigger phrase) - and keep the old
    version so the change can be undone."""
    from nova.actions import skills
    return skills.edit(name, ops, author="brain")


@tool("Go back to the version of a skill before its last change.", friendly="Undid a skill change",
      props={"name": common.S}, required=["name"])
def undo_skill_change(name):
    from nova.actions import skills
    return skills.undo(name)


@tool("Start watching the user's clicks and typing so you learn a new skill from them, when "
      "they say 'watch me', 'let me show you how' or 'learn this'. They say 'done' when finished. Passwords and "
      "codes are never recorded.", friendly="Started watching",
      hints="watch me, watch how I do this, let me show you, learn this from me, record what I do",
      props={"goal": {"type": "string", "description": "what they are going to show, in their words"}}, required=[])
def start_watching(goal=""):
    # app._execute_one answers these two: the recording lives in app.py's conversation state (recorder.py)
    return "I can only watch inside a conversation."


@tool("Stop watching the user (they said they're done showing you).", friendly="Watched you",
      hints="done, I'm finished showing you, stop watching", props={}, required=[])
def stop_watching():
    return "I can only watch inside a conversation."


@tool("Save the task you just finished as a new skill, from the steps you really did, when the "
      "user says 'remember that', 'save that as a skill' or 'next time do that'. It starts as a draft and "
      "becomes a real skill after it works twice.", friendly="Saved a skill",
      hints="remember that, save that as a skill, next time do that, make that a skill, call it",
      props={"name": {"type": "string", "description": "short, like crunchyroll-new"},
      "examples": {"type": "string", "description": "phrases the user will say to start it, comma-separated"},
      "description": {"type": "string", "description": "what it does and when to use it"}},
      required=["name", "examples"])
def save_task_as_skill(name, examples="", description=""):
    # app._execute_one answers this one: it needs the steps of the task Nova just did (state in app.py).
    return "I can only do that inside a conversation, right after finishing a task."


SIGNIN_NAME = re.compile(r"^(log\s*-?\s*in|sign\s*-?\s*in|login)$", re.I)


CONTINUE_NAME = re.compile(r"^(log\s*-?\s*in|sign\s*-?\s*in|login|continue|next)$", re.I)


def _buttons(items):
    return [i for i in items if i["type"] in ("Button", "Hyperlink", "MenuItem")]


@tool("Make sure the user is signed in on a website: does nothing if they already are, presses "
      "Log in if the browser filled the login in, otherwise opens the login box and waits while the user picks "
      "their saved login. You never see or type a password. Use it in skills, before the part that needs the "
      "account.", friendly="Checked your sign-in", risky="Sign in on a website",
      hints="make sure I am logged in, sign in to the site if needed, log in before continuing",
      props={"window": common.S, "wait_secs": common.N}, required=[])
def login_if_needed(window="", wait_secs=150):
    """Make sure the user is signed in on this site: nothing to do if they already are; if the browser has filled the
    login in, press Log in; otherwise open the login box and wait while the user picks their saved login. Never
    types, reads or returns a password, a code or a box's contents."""
    w, boxes = forms._form_boxes(window)
    if not w:
        return f"No open window matches '{window}'."
    title = w[1]
    pw = next((b for b in boxes if b["password"]), None)
    user = next((b for b in boxes if not b["password"] and b["name"] != "(text box)" and forms.LOGIN_RX.search(b["name"])), None)
    _, items = forms._scan(title, limit=300)
    signin = next((b for b in _buttons(items) if SIGNIN_NAME.match(b["name"])), None)
    if not pw and not user and not signin:
        return f"Already signed in on '{title}'."
    if not pw and not user and signin:         # only a "Log in" button so far: open the form
        forms.click_element(signin["name"], title)
        time.sleep(2.0)
        w, boxes = forms._form_boxes(title)
        if not w:
            return f"The window '{title}' went away after I pressed '{signin['name']}'."
        pw = next((b for b in boxes if b["password"]), None)
        user = next((b for b in boxes if not b["password"] and b["name"] != "(text box)" and forms.LOGIN_RX.search(b["name"])), None)
        if not pw and not user:
            return f"I pressed '{signin['name']}' in '{title}', but no login box showed up yet."
    still_on_login = True
    if user and user["filled"]:                # the browser / password manager already filled it in: just press the button
        _, items = forms._scan(title, limit=300)
        go = next((b for b in _buttons(items) if CONTINUE_NAME.match(b["name"])), None)
        if go:
            forms.click_element(go["name"], title)
            time.sleep(3.0)
            w2, boxes2 = forms._form_boxes(title)
            still_on_login = bool(w2) and any(b["password"] for b in boxes2)
    if still_on_login:                         # nothing filled it in (or it didn't work): the user picks the saved login
        out = forms.help_login(title, wait_secs)
        if out.startswith("Still on the login page"):
            return f"Still on the sign-in page in '{title}' - the user hasn't finished signing in."
    w3, boxes3 = forms._form_boxes(title)
    if w3 and any(forms_code_box(b) for b in boxes3):
        forms.wait_for_code(title, 180)
    for _ in range(60):                        # Windows Hello / passkey / security-key prompt: that part is the user's
        if not windows._find_window("Windows Security"):
            break
        time.sleep(1.0)
    w4, boxes4 = forms._form_boxes(title)
    if w4 and any(b["password"] for b in boxes4):
        return f"Still on the sign-in page in '{title}' - the user hasn't finished signing in."
    return f"Signed in on '{title}'."


def forms_code_box(b):
    from nova.actions import forms
    return bool(forms.CODE_BOX.search(b["name"]) and not forms.NOT_CODE.search(b["name"]))


@tool("List the skills (routines and recipes) the user has taught you.", friendly="Listed my skills",
      hints="what skills routines do you know, what have I taught you", props={}, required=[])
def list_skills():
    from nova.actions import skills
    return skills.describe()


@tool("Delete one of your saved skills.", friendly="Deleted a skill", risky="Delete a saved skill",
      hints="forget that routine, delete the skill", props={"name": common.S}, required=["name"])
def delete_skill(name):
    from nova.actions import skills
    return skills.delete(name)


@tool("Call this FIRST when the job is bigger than you can do well: kind='deep' for a complex, "
      "multi-step or tricky job (a stronger brain writes a full plan for you to follow), kind='code' for writing, "
      "fixing or reviewing code (a coding brain takes over). Never for simple things like opening an app, the "
      "time or chat. Takes a minute to switch.", friendly="Switched brain", core=True,
      props={"kind": {"type": "string", "enum": ["deep", "code"]}, "why": common.S}, required=["kind"])
def escalate(kind="deep", why=""):
    # app.execute answers this one itself (it switches the brain); this is only the fallback.
    return "Nothing to switch to."


@tool("You only see some of your tools right now. If none of them can do what the user asked, call "
      "this with the ability you need (e.g. 'change screen brightness', 'install an app') and you get more tools.",
      friendly="Found more tools", core=True, props={"need": common.S}, required=["need"])
def more_tools(need=""):
    # app.execute answers this one itself (it changes which tools the brain sees); this is only the fallback.
    return "All your tools are already available."
