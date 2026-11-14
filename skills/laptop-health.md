---
name: laptop-health
description: Full laptop health check - CPU, RAM, battery, temp files and top memory hogs
when: laptop health, health check, how is my laptop, system checkup
kind: recipe
---
1. Call get_system_status to check CPU, RAM, disk and battery.
2. Call temp_files_size to see how much junk can be cleaned.
3. Call list_top_processes with sort_by='memory' and limit=3.
4. Give a crisp two-sentence health report with the key numbers.
