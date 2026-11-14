---
name: movie-time
description: Clear the screen, open Netflix and turn the sound up
when: movie time, movie mode, let's watch a movie
kind: routine
---
- minimize_all_windows
- open_website url_or_search=netflix.com
- set_volume action=up, steps=10
- say Popcorn ready? Enjoy the movie!
