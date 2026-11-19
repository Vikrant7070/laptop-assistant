---
name: focus-mode
format: 2
description: Deep work setup - mute sound, dark mode on, minimize clutter and open VS Code. Use when the user wants to focus or start deep work.
examples: focus mode, deep work, coding mode, zero distractions
invoke: confirm_if_fuzzy
status: active
---
s1. minimize_all_windows
s2. set_volume action=mute
s3. set_dark_mode enabled=true
    expect: dark_mode on
    on_fail: skip
s4. open_app name="Visual Studio Code"
    expect: window_open "Visual Studio Code"
    on_fail: retry 1, then skip
s5. say Full focus mode on, yaar. Let's crush it!
