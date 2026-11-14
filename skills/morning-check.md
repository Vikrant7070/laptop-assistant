---
name: morning-check
description: A quick morning briefing - time, battery, saved notes and new notifications
when: good morning, morning check, morning briefing, start my day
kind: recipe
---
1. Call get_datetime for today's date and time.
2. Call get_system_status and note the battery and whether the disk is nearly full.
3. Call list_notes to check for any saved notes or tasks.
4. Call read_notifications with count 5.
5. Greet the user warmly and sum it all up in at most three short sentences, skipping anything empty.
