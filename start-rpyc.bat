@echo off
setlocal
chcp 65001 >nul 2>&1
set "MANAGED=%LOCALAPPDATA%\qmt-rpyc\qmt-rpyc-server.bat"

REM Prefer a configured source environment; otherwise use the managed installation.
if exist "%~dp0pyproject.toml" if exist "%~dp0.venv\Scripts\qmt-rpyc-server.exe" goto :source
if exist "%MANAGED%" (
    call "%MANAGED%" start
    goto :finished
)
echo [ERROR] Run install-server.bat first, or scripts\setup.bat for source development.
exit /b 1

:source
"%~dp0.venv\Scripts\qmt-rpyc-server.exe" --config "%~dp0.env" start

:finished
set "START_EXIT=%errorlevel%"
pause
exit /b %START_EXIT%
