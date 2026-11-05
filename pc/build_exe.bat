@echo off
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name MaiPanel --distpath ..\dist --workpath ..\build\pyinstaller --specpath ..\build --add-data "..\app\Sources\Resources;resources" panel_monitor.py
if errorlevel 1 (
  echo BUILD FAILED
  pause
  exit /b 1
)
echo Built: %~dp0..\dist\MaiPanel.exe
pause
