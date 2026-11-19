---
name: web-research
format: 2
description: Researches any topic in the background - Googles it in off-screen Chrome windows, reads the best pages, writes a report with sources. Use when the user asks Nova to research a topic or search the web for something.
examples: research, do some research on, research about, search the web for
not_for: where did you find out, where you find out, how do you know, what is the research on, look up my, find out about my, who found out
invoke: confirm_if_fuzzy
status: active
---
s1. research_in_background topic="{input}"
s2. say On it, boss! I'll research {input} in the background - you carry on, I'll tell you what I find.
