---
name: movie-time
format: 2
description: Clears the screen, turns on dark mode, sets brightness and volume, and opens Netflix. Use when the user wants to watch a movie.
examples: movie time, movie mode, let's watch a movie
invoke: confirm_if_fuzzy
status: active
---
s1. minimize_all_windows
s2. set_dark_mode enabled=true
    expect: dark_mode on
    on_fail: skip
s3. set_brightness percent=85
s4. open_website url_or_search=netflix.com
    expect: window_open "Netflix"
    on_fail: retry 1, then skip
s5. set_volume action=up, steps=10
s6. say Popcorn ready? Enjoy the movie, boss!
