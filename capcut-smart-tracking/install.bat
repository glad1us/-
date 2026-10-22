@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "PYDIR=%ROOT%\python"
set "PYVER=3.12.10"

echo === CapCut Smart Tracking: установка ===
echo Папка установки: %ROOT%
echo Всё (Python, библиотеки, временные файлы, данные) ставится сюда, а не на диск C.
echo.

if /I "%~d0"=="C:" (
  echo ВНИМАНИЕ: папка находится на диске C. Нужно около 1.5 ГБ.
  echo Рекомендуется распаковать архив на диск D, например D:\CapCutSmartTracking
  choice /M "Продолжить установку на диск C"
  if errorlevel 2 exit /b 1
)

rem --- проверка свободного места (нужно ~1500 МБ) ---
for /f %%A in ('powershell -NoProfile -Command "[math]::Floor((Get-Item -LiteralPath '%ROOT%').PSDrive.Free/1MB)"') do set FREE_MB=%%A
if defined FREE_MB if %FREE_MB% LSS 1500 (
  echo Недостаточно места на диске %~d0: свободно %FREE_MB% МБ, нужно около 1500 МБ.
  pause
  exit /b 1
)

rem --- все временные файлы и кэши на этот же диск ---
set "TEMP=%ROOT%\_tmp"
set "TMP=%ROOT%\_tmp"
set "PIP_CACHE_DIR=%ROOT%\_tmp\pip-cache"
set "PIP_NO_CACHE_DIR=1"
set "PYTHONNOUSERSITE=1"
if not exist "%TEMP%" mkdir "%TEMP%"

rem --- локальный Python (embeddable), не трогает систему и диск C ---
if not exist "%PYDIR%\python.exe" (
  echo [1/3] Загрузка Python %PYVER% в %PYDIR% ...
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol='Tls12';" ^
    "Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip' -OutFile '%TEMP%\python.zip';" ^
    "Expand-Archive -Force '%TEMP%\python.zip' '%PYDIR%';" ^
    "Set-Content -Encoding ASCII '%PYDIR%\python312._pth' 'python312.zip','.','..','import site';" ^
    "Invoke-WebRequest -UseBasicParsing 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%TEMP%\get-pip.py'" || goto :fail
  "%PYDIR%\python.exe" "%TEMP%\get-pip.py" --no-warn-script-location || goto :fail
) else (
  echo [1/3] Python уже установлен в %PYDIR%
)

echo [2/3] Установка библиотек (около 150 МБ загрузки) ...
"%PYDIR%\python.exe" -m pip install --no-warn-script-location -r "%ROOT%\requirements.txt" || goto :fail
"%PYDIR%\python.exe" -c "import cv2, numpy, PySide6.QtSvg, smart_tracking; print('Проверка: OK')" || goto :fail

echo [3/3] Ярлык на рабочем столе ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\CapCut Smart Tracking.lnk');" ^
  "$s.TargetPath='%ROOT%\run.bat'; $s.WorkingDirectory='%ROOT%'; $s.WindowStyle=7;" ^
  "$s.IconLocation='%PYDIR%\pythonw.exe,0'; $s.Save()"

rmdir /s /q "%ROOT%\_tmp" 2>nul
echo.
echo Готово. Запуск: ярлык "CapCut Smart Tracking" на рабочем столе или run.bat
echo Данные программы (пресеты, бэкапы проектов) хранятся в %ROOT%\data
pause
exit /b 0

:fail
echo.
echo Ошибка установки. Проверьте интернет и свободное место, затем запустите install.bat снова.
rmdir /s /q "%ROOT%\_tmp" 2>nul
pause
exit /b 1
