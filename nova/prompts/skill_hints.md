A voice assistant just did this task and will save its steps as a reusable skill.
Task: {goal}
Steps:
{listing}

Reply with ONLY a JSON object: {{"slots": [{{"step": "s2", "arg": "<argument name>", "find": "<the part of the argument that changes each time, e.g. a search word>"}}], "expect": {{"s2": "<check>"}}, "not_for": ["<2-4 similar requests that must NOT start this skill>"]}}. Checks you may use: window_open "text", front_window "text", page_has "text", fullscreen, dark_mode on. Use slots only for words the user would change next time. Leave a list empty if unsure.
