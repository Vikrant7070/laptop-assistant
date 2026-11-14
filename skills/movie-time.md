---
name: movie-time
description: Clear the screen, turn on dark mode, set brightness and volume, and open Netflix
when: movie time, movie mode, let's watch a movie
kind: routine
---
- minimize_all_windows
- set_dark_mode enabled=true
- set_brightness percent=85
- open_website url_or_search=netflix.com
- set_volume action=up, steps=10
- say Popcorn ready? Enjoy the movie, boss!
