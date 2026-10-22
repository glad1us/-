@echo off
rem Сборка автономного .exe (не требует Python на целевом ПК)
cd /d "%~dp0"
call .venv\Scripts\activate.bat || (echo Сначала запустите install.bat & exit /b 1)
python -m pip install pyinstaller
pyinstaller --noconfirm --windowed --paths . --name "CapCut Smart Tracking" --collect-data cv2 packaging\launcher.py
echo Результат: dist\CapCut Smart Tracking\CapCut Smart Tracking.exe
pause
