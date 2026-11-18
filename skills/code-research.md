---
name: code-research
format: 2
description: Researches coding material in the background - official docs, GitHub and Stack Overflow in off-screen Chrome windows - and writes a report with sources. Use when the user asks to research code, a library or documentation.
examples: code research, research code, find coding material, search docs for, help me research code, look up the docs, find a library for
invoke: confirm_if_fuzzy
status: active
---
s1. research_in_background topic="{input}", kind=code
s2. say Got it! I'm reading the docs, GitHub and Stack Overflow on {input} in the background - keep coding, I'll tell you when it's ready.
