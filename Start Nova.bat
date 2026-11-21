@echo off
cd /d "%~dp0"
rem Check the laptop first (python -m nova --doctor, about a second). Problems that stop Nova working are shown in a
rem message box; Nova still starts. Warnings (no GPU, a missing optional model) only show in --doctor without --quiet.
python -m nova --doctor --quiet > "%TEMP%\nova-doctor.txt" 2>&1
if errorlevel 1 powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show((Get-Content -Raw -LiteralPath \"$env:TEMP\nova-doctor.txt\") + \"`nNova will start anyway. Full report: python -m nova --doctor\", 'Nova: something needs fixing', 'OK', 'Warning')"
start "" pythonw desktop.py
