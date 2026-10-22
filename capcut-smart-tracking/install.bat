@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo === CapCut Smart Tracking: установка ===

where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" 2>nul
if errorlevel 1 (
  echo Нужен Python 3.10 или новее: https://www.python.org/downloads/windows/
  echo При установке отметьте "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist .venv (
  %PY% -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || goto :fail

rem Ярлык на рабочем столе
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\CapCut Smart Tracking.lnk');" ^
  "$s.TargetPath='%~dp0run.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"

echo.
echo Готово. Запуск: ярлык "CapCut Smart Tracking" на рабочем столе или run.bat
pause
exit /b 0

:fail
echo Ошибка установки. Проверьте подключение к интернету и повторите.
pause
exit /b 1
