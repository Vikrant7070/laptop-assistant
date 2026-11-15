---
name: vscode-git-push
description: Commit and push code to GitHub the way a person does - in VS Code's Source Control panel, not PowerShell
when: push code, push my code, push to github, commit and push, commit my changes, sync changes, upload code to github, git push
kind: recipe
---
Do this in VS Code, step by step, looking after each step. Do NOT use run_powershell for git here.
1. Call open_app with name "vscode". Call window_action focus "Visual Studio Code". If the title doesn't show the project the user means, ask which folder.
2. Call press_keys "ctrl+shift+g" to open Source Control (focus VS Code first - keys go to the window in front).
3. Call list_clickable on "Visual Studio Code" with contains "Changes" and tell the user which files changed. Nothing changed: say there's nothing to push and stop.
4. Ask the user for the commit message, or suggest a short one from the changed files and ask "Shall I commit and push with this?". Wait for a yes - pushing publishes the code.
5. Call list_clickable with contains "Message" to find the commit box, click_element it, then type_text the message into "Visual Studio Code".
6. Call press_keys "ctrl+enter" to commit. If VS Code asks to stage all changes, click_element "Yes".
7. Push: click_element "Sync Changes" (or "Publish Branch"). If no such button, press_keys "ctrl+shift+p", type_text "Git: Push", press_keys "enter".
8. Call look_at_screen on "Visual Studio Code" asking whether the push worked or shows an error or a sign-in box.
9. Sign-in box: tell the user to sign in themselves - never type passwords or tokens. Error: read it out and ask what to do; never change the git remote, credentials or SSH keys on your own.
10. Tell the user in one sentence what was pushed.
