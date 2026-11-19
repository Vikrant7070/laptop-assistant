---
name: vscode-git-push
format: 2
description: Commits and pushes code to GitHub the way a person does - in VS Code's Source Control panel, not PowerShell. Use when the user asks to push, commit or sync their code.
examples: push code, push my code, push to github, commit and push, commit my changes, sync changes, upload code to github, git push
risk: consequential
invoke: explicit_only
status: active
---
s1. open_app name="Visual Studio Code"
    expect: window_open "Visual Studio Code"
    on_fail: retry 1, then stop "VS Code didn't open"
s2. window_action action=focus, title_or_app="Visual Studio Code"
    expect: front_window "Visual Studio Code"
    on_fail: retry 1, then stop "I couldn't bring VS Code to the front"
s3. press_keys keys=ctrl+shift+g
s4. brain "Find out which files changed: list the clickable items in the Source Control panel containing 'Changes'. Tell the user which files changed. If nothing changed, answer starting with STOP: there is nothing to push." tools: list_clickable
s5. brain "Suggest a short commit message (under 60 characters) from the changed files. Answer with only the message." slot: message
s6. confirm "Commit and push to GitHub with the message '{message}'?"
s7. brain "In VS Code's Source Control panel click the commit message box, type this commit message: {message} - then press ctrl+enter to commit. If VS Code asks to stage all changes, click Yes." tools: list_clickable, click_element, type_text, press_keys
s8. brain "Push: click 'Sync Changes' (or 'Publish Branch'). If there is no such button, press ctrl+shift+p, type 'Git: Push' and press enter." tools: click_element, list_clickable, press_keys, type_text
s9. brain "Look at VS Code and say whether the push worked, failed or shows a sign-in box. Never type a password or token: if there is a sign-in box, tell the user to sign in themselves." tools: look_at_screen
s10. report "Tell the user in one sentence what was pushed, or what went wrong."
