---
name: anime-time
format: 2
description: Sets up Crunchyroll for watching in Chrome - dark mode, open the site, sign in if needed, fullscreen. Use only when the user asks to start anime time or to set up / open Crunchyroll to watch.
examples: anime time, set up crunchyroll, crunchyroll experience, crunchyroll fullscreen, fullscreen crunchyroll, make crunchyroll fullscreen, let's watch anime on crunchyroll
not_for: check the latest anime, what new anime came out, which anime should i watch, recommend an anime, latest anime episode, who voices gojo
requires_any: anime time, crunchyroll
invoke: confirm_if_fuzzy
status: active
---
s1. set_dark_mode enabled=true
    expect: dark_mode on
    on_fail: skip
s2. open_website url_or_search=https://www.crunchyroll.com
    expect: window_open "Crunchyroll"
    on_fail: retry 1, then stop "Crunchyroll didn't open"
s3. login_if_needed window=Crunchyroll
    on_fail: ask "I couldn't sign in to Crunchyroll - sign in yourself and I'll carry on. Ready?"
s4. window_action action=focus, title_or_app=Crunchyroll
    expect: front_window "Crunchyroll"
    on_fail: skip
s5. press_keys keys=f
    expect: fullscreen
    on_fail: brain "Make the Crunchyroll video player fullscreen" , then skip
s6. say Crunchyroll is ready, boss. Enjoy the anime!
