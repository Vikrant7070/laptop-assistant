"""Which brain thinks: "local first, cloud when stuck" (docs/plans/local-first-brain.md, Part B), and the brain
facts the Model dialog shows (ready_info).

settings "brain_mode" (only matters when a cloud provider with a key is chosen):
  cloud_first - the cloud brain answers everything; the local model takes over when the cloud fails (as before).
  local_first - every request starts on the local brains (everyday model, then its planner / coder). A task climbs to
                the cloud brain only when a "stuck" sign fires, stays there until it ends, and the next request starts
                local again. Side jobs (notification wording, learning, skills) call config.brain(), which is the local
                model in this mode, so they never spend free cloud uses.

Stuck signs (numbers in config/model_routing.json, see router.DEFAULTS):
  - the user asks for the cloud ("ask Gemini", "use the cloud brain")
  - the local brain calls escalate(kind="cloud")
  - the local planner gives no usable plan
  - code read back a step as not done, cloud_after_failed_checks times (4c read-back)
  - the Judge said "not done", she was pushed once, and still stopped with it unfinished
  - the same tool call with the same result REPEAT_LIMIT times in a row
  - cloud_after_turns brain turns without finishing
  - the local brain says it can't, on a task where it already used tools
A task climbs at most once. If the cloud model's free uses are used up (nova/brain/usage.py) she stays local and says so.
"""


import re
import time

from nova.brain import cloud, llm, router, usage
from nova.avatar import body as body_module
from nova import intents, protocol, task_state


MODES = ("cloud_first", "local_first")

CANT = re.compile(r"\b(i can'?t|i cannot|i'?m (not able|unable)|i am (not able|unable)|i don'?t know how|"
                  r"i couldn'?t|i wasn'?t able|i was unable|i'?m stuck)\b", re.I)

CLOUD_TAKES_OVER = ("You are now the cloud brain: the local brain got stuck on this task. Read what was tried so far "
                    "and carry the task on with your tools.")


def cloud_helper(s):
    """The cloud model a local-first task may climb to ("cloud:google:..."), or None (other mode, no key, no model)."""
    if s.get("brain_mode") != "local_first":
        return None
    p = s.get("provider", "ollama")
    name = (s.get("cloud_models") or {}).get(p) or cloud.DEFAULT_MODELS.get(p)
    return cloud.model_id(p, name) if p in cloud.PROVIDERS and name and cloud.get_key(p) else None


def climb(task, why):
    """Hand this task to the cloud brain. True when it did; False when it can't (other mode, already climbed, no key,
    free uses used up - said once)."""
    s = config.settings()
    target = cloud_helper(s)
    if not task or not target or task.get("climbed") or cloud.parse(conversation.task_brain(s, task)):
        return False
    if not s.get("email_cloud") and intents.EMAIL.search(config._unspoken(task.get("goal", ""))):
        conversation.task_log(f"CLIMB refused ({why}): mail stays on this laptop (email_cloud is off)")
        return False                            # her mail never goes to a cloud brain unless ⚙ allows it
    provider, name = cloud.parse(target)
    out = usage.blocked(provider, name)
    if out or llm.cloud_resting():
        if not task.get("climb_refused"):
            task["climb_refused"] = True
            session.think(f"☁️ I'd ask {name} for help ({why}), but " + (
                out[0].lower() + out[1:] if out else "it failed a moment ago, so I'm staying with my local brain."))
            conversation.task_log(f"CLIMB refused ({why}): {out or 'cloud resting'}")
        return False
    task.update(model=target, climbed=why, routed=True, started=time.time())
    task.pop("answer_only", None)               # the coder's "words only" doesn't bind the cloud brain
    did = "; ".join(task.get("did", [])[-8:]) or "no steps yet"
    task["facts"] = (task.get("facts", "") + f"\n\nHANDED OVER TO YOU: the local brain got stuck on this task ({why}). "
                     f"Steps it did: {did}. Check what is already done, then finish the task: {task.get('goal', '')}")
    msgs = session.state.get("messages") or []
    if msgs and msgs[-1].get("role") == "assistant" and not msgs[-1].get("tool_calls"):
        # Her last answer ends the history; Gemini refuses a request that ends with the model's own turn (400).
        msgs.append({"role": "user", "content": protocol.note(
            "The local brain stopped here without finishing. You are the cloud brain now: finish the task.")})
    conversation.task_log(f"CLIMB -> {target}: {why}")
    session.think(f"☁️ My local brain is stuck ({why}) → asking {name} in the cloud.")
    return True


def at_start(task, s):
    """The user asked for the cloud brain in the request itself: start this task there."""
    if task and not task.get("climb_checked"):
        task["climb_checked"] = True
        if intents.ASK_CLOUD.search(task.get("goal", "")) and cloud_helper(s):
            climb(task, "you asked for it")


def after_step(task, s, same_calls, turns):
    """After a batch of steps: climb if the task looks stuck. True when it climbed (the loop restarts its counters)."""
    if not task or not cloud_helper(s) or task.get("climbed"):
        return False
    o = router.options(s)
    bad = task_state.failing(task_state.of(task))
    if len(bad) >= o["cloud_after_failed_checks"]:
        return climb(task, f"{len(bad)} steps didn't do what they should")
    if same_calls >= conversation.REPEAT_LIMIT:
        return climb(task, f"the same step {same_calls} times with no change")
    if turns >= o["cloud_after_turns"]:
        return climb(task, f"{turns} steps and still not finished")
    return False


def at_answer(task, text, s):
    """She is about to stop. Climb instead when the Judge's "not done" stands after one push, or when she says she
    can't, on a task where she already used tools. True when it climbed."""
    if not task or not cloud_helper(s) or task.get("climbed"):
        return False
    if task.get("nudged") and task.get("done") is False:
        return climb(task, "my checks still say it isn't done")
    if (task.get("calls") or task.get("steps")) and CANT.search(text or ""):
        return climb(task, "it said it couldn't do it")
    return False


def ready_info():
    s = config.settings()
    models = llm.list_models()
    model = s["model"]
    active = config.brain(s)
    info = {"avatar_roam": body_module.roam_mode(s), "routing": {k: v for k, v in router.options(s).items() if k in router.ROUTING_KEYS},
            "provider": s.get("provider", "ollama"), "cloud_models": s.get("cloud_models") or {},
            "cloud_defaults": cloud.DEFAULT_MODELS, "keys": cloud.has_keys(), "brain": active,
            "brain_mode": s.get("brain_mode", "cloud_first"),
            "providers": {k: v["label"] for k, v in cloud.PROVIDERS.items()}}
    on_cloud = active != model
    if models is None:
        return {"ready": on_cloud, "ollama": False, "models": [], "model": model, **info}
    installed = model in models or f"{model}:latest" in models
    return {"ready": installed or on_cloud, "ollama": True, "models": models, "model": model,
            "tools": llm.supports_tools(model) if installed else None, **info}


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import config, conversation, session  # noqa: E402
