@echo off
rem One-click DSH launcher: start if not running, then open browser
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\claude-code\scripts\dsh-start.ps1" > "%TEMP%\dsh-launch.log" 2>&1
start "" "http://127.0.0.1:3080"
