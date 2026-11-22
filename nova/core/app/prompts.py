"""Who Nova is and what the brain is told: persona, system prompt, the tools offered, activity labels."""


from nova import prompts as promptfiles
from nova import intents
from nova.brain import cloud
from nova.core import characters
from nova.actions import knowledge
from nova.brain import llm
from nova.actions import mcp
from nova.brain import toolpick
from nova.brain import router
from nova.actions import tools
from nova.audio import voice


PERSONA = promptfiles.load("system")


TALK_ENGLISH = promptfiles.load("talk_english")


TALK_MIXED = promptfiles.load("talk_mixed")


HEAR_MIXED = promptfiles.load("hear_mixed")


def persona_text(cid=None):
    """Her instructions for the chosen character (settings "character")."""
    ch = characters.get(cid or config.settings().get("character"))
    p, name = ch["persona"], ch["name"]
    fill = lambda t: (t or "").replace("{NAME}", name).replace("{DESI_TALK}", (TALK_ENGLISH if voice.ENGLISH_ONLY else TALK_MIXED).strip())
    intro = fill(p.get("intro")) or f"You are {name}, who lives inside the user's Windows laptop and looks after it."
    intro += (f'\nYour name is {name} - always. When asked who you are, say "{name}". The user wakes you by '
              f'saying "Hey {name}". {name} is YOUR name, not the user\'s: never greet or address the user as '
              f'"{name}" (no "Hey {name}").')
    talk = fill(p.get("talk")).rstrip() + "\n" if p.get("talk") else ""
    return (PERSONA.replace("{INTRO}", intro).replace("{TALK}", talk).replace("{TOUCH}", fill(p.get("touch")))
            .replace("{HEAR}", "" if voice.ENGLISH_ONLY else HEAR_MIXED))


def wake_phrase():
    """What the user says to call her: "Hey Lucy" for Lucy."""
    return f'Hey {characters.get(config.settings().get("character"))["name"]}'


# How hard her brain thinks first (settings "reasoning"): low / high / max, or auto = low for everyday chat and
# quick actions, high for questions that need working out and for long or failing tasks. The same choice also turns
# a local thinking model's "think" on, so the backup brain follows it too.
REASONING_CHOICES = ("auto", *cloud.LEVELS)


def reasoning_level(s, task=None):
    pick = s.get("reasoning", "auto")
    if pick in cloud.LEVELS:
        return pick
    goal = (task or {}).get("goal", "")
    if task and (task.get("steps", 0) >= 3 or task.get("calls", 0) >= 4 or task.get("done") is False):   # a long task, or a check said "not done"
        return "high"
    # intents.HARD holds the built-in words; the router's deep_triggers (config/model_routing.json, user-editable) count
    # too, so a word added there for local planning also makes a cloud brain think harder - one list to edit.
    return "high" if len(goal) > 160 or intents.HARD.search(goal) or router.deep_wanted(goal) else "low"


def active_tools(model):
    """The tool schemas the brain sees this request. A local model gets the set picked for the request (toolpick.py:
    a shorter prompt makes the small model on the laptop GPU much faster); a cloud brain gets all of them."""
    task = session.state.get("task")
    off = {"escalate"} if cloud.parse(model) or not router.options(config.settings())["auto_route"] else set()
    if cloud.parse(model) or not task or not task.get("tools"):
        every = tools.TOOLS + mcp.tools_list()          # + 🔌 connector tools
        return [t for t in every if t["name"] not in {"more_tools"} | off] if cloud.parse(model) else [
            t for t in every if t["name"] not in off]
    return [t for t in toolpick.schemas(task["tools"]) if t["name"] not in off]


def system_prompt(model):
    # Who the user is + lessons from her own mistakes (data/knowledge.json), picked once per request for what it's
    # about. Fixed for the whole task, so Ollama can reuse the prompt it has already read between steps.
    task = session.state.get("task")
    facts = task["facts"] if task and "facts" in task else knowledge.prompt_block()
    proto = "" if llm.supports_tools(model) else llm.text_protocol(active_tools(model))
    # Without this she guesses her model from the chat history and repeats an old answer after a switch.
    c = cloud.parse(model)
    which = (f"{c[1]} ({cloud.PROVIDERS[c[0]]['label'].split()[0]}, in the cloud; if it fails, {config.settings()['model']} "
             "on this laptop takes over)") if c else f"{model} (running locally on this laptop via Ollama)"
    brain_line = (f"\n\nYour brain (the AI model you run on) right now is {which}. If asked which model you are, say "
                  "exactly this - it may differ from what you said earlier in this chat.")
    return persona_text() + facts + brain_line + proto


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import config, session  # noqa: E402
