@echo off
rem Daily pull of the latest encrypted VPS backup (offsite copy).
rem Scheduled by Task Scheduler at 12:30. Keep the newest 7 copies.
rem
rem 2026-09-11 fixes (three):
rem   1) Line 15 used to be  "%DST%\"  -- the trailing backslash escaped the
rem      closing quote, so scp received a malformed path. It had been failing
rem      every day since 2026-09-06 and nobody noticed. Now uses forward slash.
rem   2) Rotation used forfiles /d -7 (by AGE). Now keeps the newest 7 by COUNT.
rem   3) New: after a successful pull, write /root/.offsite-ok on the VPS so
rem      doctor.sh / the morning check can see whether the offsite hop is alive.
rem
rem NOTE: keep this file ASCII-only. cmd.exe reads .cmd with the OEM codepage
rem (GBK on a Chinese Windows); UTF-8 Chinese comments break line parsing.
setlocal
set DST=D:\claude-code\backups\vps
set LOG=%DST%\pull.log
set KEEP=7
rem Host by NAME, not IP (2026-09-16). 43.133.211.140 is a dead address since
rem the 09-15 rebind; commit 286160c missed this file (and deploy.ps1, fixed in
rem dcf1da2). It would still fail loudly -- empty NAME exits 1 -- but the error
rem reads "no backup found on VPS", which points at the wrong cause.
set VPS=root@noxtang.com
if not exist "%DST%" mkdir "%DST%"

for /f "usebackq delims=" %%i in (`ssh -o "BatchMode=yes" -o "ConnectTimeout=30" %VPS% "readlink /root/backups/auto/latest.tar.gz.enc"`) do set NAME=%%i
if "%NAME%"=="" (
  echo [%date% %time%] ERROR: no backup found on VPS >> "%LOG%"
  exit /b 1
)

rem Use forward slashes: "%DST%/%NAME%" . Never write "%DST%\" (see fix 1).
scp -o "BatchMode=yes" -q %VPS%:/root/backups/auto/%NAME% "%DST%/%NAME%" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] scp FAILED: %NAME% >> "%LOG%"
  exit /b 1
)

rem Keep the newest %KEEP% by count. Never delete the newest one.
for /f "skip=%KEEP% delims=" %%f in ('dir /b /o-d "%DST%\caelum-*.tar.gz.enc"') do del "%DST%\%%f" >nul 2>&1

rem Heartbeat so the VPS side can tell the offsite hop is alive.
ssh -o "BatchMode=yes" -o "ConnectTimeout=30" %VPS% "date -Iseconds > /root/.offsite-ok" >nul 2>&1

echo [%date% %time%] OK %NAME% >> "%LOG%"
exit /b 0
