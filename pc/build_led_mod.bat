@echo off
setlocal
rem Build pc\ledmod\MaiPanelLed.cs and install it into the game Mods folder.
rem Usage: build_led_mod.bat <game folder>   (the folder that contains Sinmai_Data)

set "GAME=%~1"
if "%GAME%"=="" goto no_game_arg
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
set "MELON=%GAME%\MelonLoader\net35"
set "MANAGED=%GAME%\Sinmai_Data\Managed"

if not exist "%CSC%" goto no_csc
if not exist "%MELON%\MelonLoader.dll" goto no_melon
if not exist "%MANAGED%\Assembly-CSharp.dll" goto no_game

if not exist "%GAME%\Mods" mkdir "%GAME%\Mods"
if exist "%GAME%\Mods\MaiPanelLed.dll" copy /y "%GAME%\Mods\MaiPanelLed.dll" "%GAME%\Mods\MaiPanelLed.dll.bak" >nul

"%CSC%" /nologo /target:library /langversion:5 /out:"%GAME%\Mods\MaiPanelLed.dll" /r:"%MELON%\MelonLoader.dll" /r:"%MELON%\0Harmony.dll" /r:"%MANAGED%\Assembly-CSharp.dll" /r:"%MANAGED%\UnityEngine.CoreModule.dll" /r:System.dll /r:System.Core.dll "%~dp0ledmod\MaiPanelLed.cs"
if errorlevel 1 goto failed

echo.
echo [OK] Installed: %GAME%\Mods\MaiPanelLed.dll
pause
exit /b 0

:no_csc
echo [ERROR] Compiler not found: %CSC%
pause
exit /b 1

:no_melon
echo [ERROR] MelonLoader not found under: %GAME%
pause
exit /b 1

:no_game
echo [ERROR] Assembly-CSharp.dll not found under: %MANAGED%
pause
exit /b 1

:failed
echo.
echo [ERROR] Compile failed, game Mods folder left unchanged.
pause
exit /b 1

:no_game_arg
echo [ERROR] Game folder not given.
echo Usage: build_led_mod.bat ^<game folder^>
pause
exit /b 1
