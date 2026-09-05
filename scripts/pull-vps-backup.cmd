@echo off
rem Daily pull of the latest encrypted VPS backup (offsite copy).
rem Scheduled by Task Scheduler at 12:30. Keep last 7 copies.
setlocal
set DST=D:\claude-code\backups\vps
set LOG=%DST%\pull.log
if not exist "%DST%" mkdir "%DST%"

for /f "usebackq delims=" %%i in (`ssh -o "BatchMode=yes" -o "ConnectTimeout=30" root@43.133.211.140 "readlink /root/backups/auto/latest.tar.gz.enc"`) do set NAME=%%i
if "%NAME%"=="" (
  echo [%date% %time%] ERROR: no backup found on VPS >> "%LOG%"
  exit /b 1
)

scp -o "BatchMode=yes" -q root@43.133.211.140:/root/backups/auto/%NAME% "%DST%\" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] scp FAILED: %NAME% >> "%LOG%"
  exit /b 1
)

forfiles /p "%DST%" /m caelum-*.tar.gz.enc /d -7 /c "cmd /c del @path" >nul 2>&1
echo [%date% %time%] OK %NAME% >> "%LOG%"
exit /b 0
