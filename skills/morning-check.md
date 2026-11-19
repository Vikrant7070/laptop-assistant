---
name: morning-check
format: 2
description: A quick morning briefing - time, battery, saved notes and new notifications. Use when the user greets Nova in the morning or asks for their morning briefing.
examples: good morning, morning check, morning briefing, start my day
risk: read_only
invoke: confirm_if_fuzzy
status: active
---
s1. get_datetime
s2. get_system_status
s3. list_notes
s4. read_notifications count=5
s5. report "Greet the user warmly and sum it all up in at most three short sentences (date and time, battery or a nearly full disk, notes or tasks, new notifications). Skip anything empty."
