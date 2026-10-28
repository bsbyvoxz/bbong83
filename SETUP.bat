@echo off
setlocal
cd /d "%~dp0"
if exist .venv\Scripts\python.exe goto install
where py >nul 2>nul
if errorlevel 1 goto plain_python
py -3.12 -c "import sys" >nul 2>nul
if errorlevel 1 goto default_py
py -3.12 -m venv .venv
if errorlevel 1 goto failed
goto install
:default_py
py -3 -m venv .venv
if errorlevel 1 goto failed
goto install
:plain_python
python -m venv .venv
if errorlevel 1 goto failed
:install
.venv\Scripts\python.exe -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,14), 'Use Python 3.10-3.13 (3.12 recommended)'"
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m unittest discover -s tests -v
if errorlevel 1 goto failed
echo Setup complete. Install AutoHotkey v1.1.37.02 and run START.bat.
pause
exit /b 0
:failed
echo Setup failed. Read the error above. Python 3.12 is recommended.
pause
exit /b 1
