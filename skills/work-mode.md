---
name: work-mode
description: Get the laptop ready for work - clear the screen, open the work apps, dark mode on
when: start work, work mode, let's get going, time to work
kind: routine
---
- minimize_all_windows
- open_app name="Visual Studio Code"
- open_website url_or_search=mail.google.com
- set_dark_mode enabled=true
- say Ready when you are, boss!
