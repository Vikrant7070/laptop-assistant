---
name: youtube-search
format: 2
description: Searches YouTube for something the user names. Use when the user asks to search or find something on YouTube.
examples: search youtube for, find on youtube, youtube search
invoke: confirm_if_fuzzy
status: active
---
s1. open_website url_or_search="https://www.youtube.com/results?search_query={input_url}"
    expect: window_open "YouTube"
    on_fail: retry 1, then skip
s2. say Here's what YouTube has for {input}.
