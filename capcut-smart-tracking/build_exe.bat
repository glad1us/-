@echo off
chcp 65001 >nul
rem Сборка автономного .exe в папку dist (на том же диске, что и программа)
cd /d "%~dp0"
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
if not exist "%ROOT%\python\python.exe" (echo Сначала запустите install.bat & pause & exit /b 1)
set "TEMP=%ROOT%\_tmp"
set "TMP=%ROOT%\_tmp"
set "PIP_NO_CACHE_DIR=1"
if not exist "%TEMP%" mkdir "%TEMP%"
"%ROOT%\python\python.exe" -m pip install --no-warn-script-location pyinstaller || goto :end
"%ROOT%\python\python.exe" -m PyInstaller --noconfirm --windowed --paths "%ROOT%" ^
  --workpath "%ROOT%\_tmp\build" --distpath "%ROOT%\dist" --specpath "%ROOT%\_tmp" ^
  --name "CapCut Smart Tracking" --collect-data cv2 "%ROOT%\packaging\launcher.py"
echo Результат: %ROOT%\dist\CapCut Smart Tracking\CapCut Smart Tracking.exe
:end
rmdir /s /q "%ROOT%\_tmp" 2>nul
pause
