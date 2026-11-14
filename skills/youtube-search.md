---
name: youtube-search
description: Search YouTube for something
when: search youtube for, find on youtube, youtube search
kind: routine
---
- open_website url_or_search="https://www.youtube.com/results?search_query={input_url}"
- say Here's what YouTube has for {input}.
