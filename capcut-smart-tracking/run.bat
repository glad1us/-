@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
if not exist "%ROOT%\python\pythonw.exe" (
  echo Сначала запустите install.bat
  pause
  exit /b 1
)
rem данные и временные файлы программы — в папке установки, не на диске C
set "SMART_TRACKING_HOME=%ROOT%\data"
set "TEMP=%ROOT%\data\tmp"
set "TMP=%ROOT%\data\tmp"
if not exist "%TEMP%" mkdir "%TEMP%"
start "" "%ROOT%\python\pythonw.exe" -m smart_tracking %*
