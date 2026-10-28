@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
 echo Run SETUP.bat first.
 pause
 exit /b 1
)
if not exist GAME_INPUT_AHKv1.ahk (
 echo Missing GAME_INPUT_AHKv1.ahk
 pause
 exit /b 1
)
start "" "%~dp0GAME_INPUT_AHKv1.ahk"
.venv\Scripts\python.exe polar_v18.py
set "polar_exit=%errorlevel%"
pause
exit /b %polar_exit%
