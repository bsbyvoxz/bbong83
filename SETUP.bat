@echo off
setlocal
cd /d "%~dp0"
set "polar_python_cmd="
rem Probe installed compatible versions instead of trusting py's default runtime.
call :try_python ".venv\Scripts\python.exe"
call :try_python py -3.12
call :try_python py -3.13
call :try_python py -3.11
call :try_python py -3.10
call :try_python python
call :try_python python3
if defined polar_python_cmd goto run_setup

rem Supported by the current Python Install Manager. Older launchers may fail;
rem that case gets explicit manual installation instructions below.
where py >nul 2>nul
if errorlevel 1 goto missing_runtime
echo No compatible installed Python found. Installing Python 3.12 with Python Install Manager...
py install 3.12
if errorlevel 1 goto missing_runtime
call :try_python py -3.12
if not defined polar_python_cmd goto missing_runtime

:run_setup
%polar_python_cmd% setup_environment.py
set "polar_setup_exit=%errorlevel%"
if not "%polar_setup_exit%"=="0" goto failed
pause
exit /b 0

:missing_runtime
echo.
echo Python 3.10-3.13 64-bit is required. Python 3.12 is recommended.
echo If Python Install Manager is installed, run: py install 3.12
echo Otherwise install Python 3.12 64-bit from https://www.python.org/downloads/windows/
echo Then run SETUP.bat again. Existing project files are preserved.
pause
exit /b 1

:failed
echo.
echo Setup failed. Read the specific error above. START.bat has not been run.
pause
exit /b %polar_setup_exit%

:try_python
if defined polar_python_cmd exit /b 0
%* -c "import sys,struct; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,14) and struct.calcsize('P') * 8 == 64 else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "polar_python_cmd=%*"
exit /b 0
