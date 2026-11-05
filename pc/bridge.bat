@echo off
cd /d "%~dp0"
python bridge.py --serial COM33 --usb %*
if errorlevel 1 pause
