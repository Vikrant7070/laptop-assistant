---
name: work-mode
format: 2
description: Gets the laptop ready for work - clear the screen, open the work apps, dark mode on. Use when the user says to start work.
examples: start work, work mode, let's get going, time to work
invoke: confirm_if_fuzzy
status: active
---
s1. minimize_all_windows
s2. open_app name="Visual Studio Code"
    expect: window_open "Visual Studio Code"
    on_fail: retry 1, then skip
s3. open_website url_or_search=mail.google.com
s4. set_dark_mode enabled=true
    expect: dark_mode on
    on_fail: skip
s5. say Ready when you are, boss!
