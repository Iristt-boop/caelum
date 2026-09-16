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

rem Expected size, asked BEFORE the transfer. This is the only trustworthy
rem yardstick -- comparing against "whatever arrived" proves nothing.
rem NOTE: write %%s here, not %%%%s. Measured 2026-09-16: %%s reaches the
rem remote shell as %s (correct); %%%%s arrives as the literal string "%s".
for /f "usebackq delims=" %%i in (`ssh -o "BatchMode=yes" -o "ConnectTimeout=30" %VPS% "stat -c %%s /root/backups/auto/%NAME%"`) do set WANT=%%i
if "%WANT%"=="" (
  echo [%date% %time%] ERROR: cannot stat %NAME% on VPS >> "%LOG%"
  exit /b 1
)

rem 2026-09-16 fix (four): download to .part, rename only after it verifies.
rem
rem Before this, scp wrote straight to the final name. On 09-12 the run was
rem interrupted mid-transfer (the log has neither OK nor FAILED for that day --
rem the process died before it could write either), leaving a 3.1 MB stub
rem wearing the real filename while the VPS copy was 17.4 MB. So `dir` showed
rem a backup for 09-12 that could not be opened, and nothing anywhere said so.
rem
rem A .part file makes an interruption harmless: the real name only ever holds
rem a file that finished AND matched the expected byte count.
rem Use forward slashes: "%DST%/%NAME%" . Never write "%DST%\" (see fix 1).
del "%DST%\%NAME%.part" >nul 2>&1
scp -o "BatchMode=yes" -q %VPS%:/root/backups/auto/%NAME% "%DST%/%NAME%.part" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] scp FAILED: %NAME% >> "%LOG%"
  del "%DST%\%NAME%.part" >nul 2>&1
  exit /b 1
)

rem Byte-for-byte size check. scp can exit 0 on a short write; size cannot lie.
set GOT=0
for %%A in ("%DST%\%NAME%.part") do set GOT=%%~zA
if not "%GOT%"=="%WANT%" (
  echo [%date% %time%] SIZE MISMATCH %NAME%: got %GOT% want %WANT% >> "%LOG%"
  del "%DST%\%NAME%.part" >nul 2>&1
  exit /b 1
)

move /y "%DST%\%NAME%.part" "%DST%\%NAME%" >nul
if errorlevel 1 (
  echo [%date% %time%] ERROR: cannot rename %NAME%.part >> "%LOG%"
  exit /b 1
)

rem Keep the newest %KEEP% by count. Never delete the newest one.
for /f "skip=%KEEP% delims=" %%f in ('dir /b /o-d "%DST%\caelum-*.tar.gz.enc"') do del "%DST%\%%f" >nul 2>&1

rem Heartbeat so the VPS side can tell the offsite hop is alive.
ssh -o "BatchMode=yes" -o "ConnectTimeout=30" %VPS% "date -Iseconds > /root/.offsite-ok" >nul 2>&1

echo [%date% %time%] OK %NAME% >> "%LOG%"
exit /b 0
