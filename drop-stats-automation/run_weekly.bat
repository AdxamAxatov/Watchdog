@echo off
REM Wrapper invoked by Windows Task Scheduler.
REM Runs run_report.py with the system Python. Edit PYTHON_EXE if needed.

setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE=python"

cd /d "%SCRIPT_DIR%"
"%PYTHON_EXE%" "%SCRIPT_DIR%run_report.py"
exit /b %ERRORLEVEL%
