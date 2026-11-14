---
name: morning-check
description: A quick morning briefing - time, battery and anything new
when: good morning, morning check, morning briefing
kind: recipe
---
1. Call get_datetime for today's date and time.
2. Call get_system_status and note only the battery and whether the disk is nearly full.
3. Call read_notifications with count 5.
4. Greet the user warmly and sum it all up in at most three short sentences. Skip anything that isn't worth mentioning.
