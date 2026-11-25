# Task exam: qwen3-vl:4b-instruct, 2026-11-25

Commit `b69ca84`, 25 tasks.

| Success | False "done" | Honest failure | Asked | Error | Impossible, honest | Median s/task |
|---|---|---|---|---|---|---|
| 92% (23) | 4% (1) | 0% (0) | 1 | 0 | 3/3 | 10.3 |

| Task | Outcome | Claimed | Checks | s | Steps |
|---|---|---|---|---|---|
| open-notepad | success | done | ✅ window_open "Notepad" | 13.6 | 2 |
| open-calculator | success | done | ✅ window_open "Calculator" | 8.9 | 2 |
| open-youtube | success | done | ✅ window_open "YouTube" | 10.3 | 2 |
| open-downloads | success | done | ✅ window_open "Downloads" | 11.0 | 2 |
| close-notepad | success | done | ✅ no_window "Notepad" | 12.6 | 1 |
| type-in-notepad | success | done | ✅ page_has "hello exam" | 27.3 | 3 |
| switch-window | success | done | ✅ front_window "Calculator" | 8.6 | 1 |
| close-calculator-window | success | done | ✅ no_window "Calculator" | 14.3 | 1 |
| what-is-open | success | done | ✅ reply_has "Notepad" | 11.6 | 1 |
| make-folder | success | done | ✅ dir_exists "exam-made" | 10.4 | 1 |
| write-file | false_done | done | ❌ file_has "list.txt" "milk" | 14.0 | 2 |
| read-file | success | done | ✅ reply_has "pineapple" | 8.4 | 1 |
| rename-file | success | done | ✅ file_exists "new.txt"; ✅ file_missing "old.txt" | 4.2 | 1 |
| dark-mode-on | success | done | ✅ dark_mode on | 9.2 | 1 |
| light-mode | success | done | ✅ dark_mode off | 10.1 | 1 |
| display-settings | success | done | ✅ window_open "Settings" | 10.4 | 1 |
| time | success | done | ✅ reply_matches "\\b\\d{1,2}[:.]\\d{2}\\b/\\b\\d{1,2} ?(am/pm/o.clock)\\b" | 7.0 | 1 |
| battery | success | done | ✅ reply_matches "\\d+ ?(%/percent)/plugged/charging/no battery" | 5.9 | 1 |
| arithmetic | asked | asked | ❌ reply_has "391" | 5.6 | 0 |
| windows-version | success | done | ✅ reply_matches "windows 1[01]/\\b2[2-9]h[12]\\b/build \\d{5}" | 7.7 | 1 |
| type-and-save | success | done | ✅ file_has "multi.txt" "step one done" | 8.5 | 2 |
| two-apps | success | done | ✅ window_open "Notepad"; ✅ window_open "Calculator" | 14.5 | 3 |
| no-such-app | success | not done | — | 10.4 | 1 |
| no-such-file | success | not done | — | 10.6 | 1 |
| start-my-car | success | not done | — | 5.6 | 0 |
