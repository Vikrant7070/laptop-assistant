---
name: speed-up-laptop
format: 2
description: Cleans and speeds up the laptop - checks RAM and the pagefile, deletes old temp files, turns off startup apps that aren't needed, then says which big programs to close. Use when the user asks to clean, speed up or fix a slow laptop.
examples: speed up my laptop, clean my laptop, make my laptop fast, my laptop is slow, laptop is lagging, clean up the laptop, free up ram, laptop tune up
not_for: how is my laptop, laptop health, health check, how much ram do i have, buy more ram, clean my desk, clean the screen
requires_any: speed, slow, lag, clean, fast, tune, free up
invoke: confirm_if_fuzzy
status: active
---
s1. check_memory
s2. temp_files_size
s3. clean_temp_files
    on_fail: skip
s4. set_startup_app name=extras, enabled=false
    on_fail: skip
s5. list_top_processes sort_by=memory, limit=5
s6. report "In at most four short sentences: how full memory is (RAM and the SSD pagefile), how much temp space was freed, which startup apps were turned off, and the one or two biggest programs the user could close to speed things up (never close them yourself). Say a bigger pagefile won't make it faster - closing programs will."
