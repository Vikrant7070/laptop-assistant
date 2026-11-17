---
name: anime-time
description: Setup anime viewing in Chrome: turn on dark mode, open Crunchyroll, handle login if needed, focus window, and fullscreen
when: anime time, crunchyroll experience, crunchyroll whole experience, fullscreen crunchyroll, crunchyroll fullscreen, make crunchyroll fullscreen
kind: recipe
---
- set_dark_mode enabled=true
- open_app name=chrome
- open_website url_or_search=https://www.crunchyroll.com
- Check Crunchyroll window to see if user is logged in
- If not logged in, click Log In, submit saved autofill credentials, and press Enter
- window_action action=focus, title_or_app=Chrome
- press_keys keys=f
- say Wah boss, Crunchyroll is ready! Enjoy the anime!
