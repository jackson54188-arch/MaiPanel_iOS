@echo off
cd /d "%~dp0"
python bridge.py --serial COM33 --serial2 COM34 --usb %*
if errorlevel 1 pause
