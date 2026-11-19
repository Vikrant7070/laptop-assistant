---
name: laptop-health
format: 2
description: Full laptop health check - CPU, RAM, battery, temp files and top memory hogs. Use when the user asks how the laptop is doing or for a health check.
examples: laptop health, health check, how is my laptop, system checkup
risk: read_only
invoke: confirm_if_fuzzy
status: active
---
s1. get_system_status
s2. temp_files_size
s3. list_top_processes sort_by=memory, limit=3
s4. report "Give a crisp two-sentence health report with the key numbers (CPU, RAM, battery, temp files, the top memory hogs)."
