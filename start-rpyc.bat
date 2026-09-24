@echo off
setlocal
chcp 65001 >nul 2>&1
set "MANAGED=%LOCALAPPDATA%\qmt-rpyc\qmt-rpyc-server.bat"

REM A source checkout takes precedence over a separate managed installation.
if exist "%~dp0pyproject.toml" goto :source
if exist "%MANAGED%" (
    call "%MANAGED%" start
    goto :finished
)
echo [ERROR] Run install-server.bat from the extracted Windows bundle first.
exit /b 1

:source
if not exist "%~dp0.venv\Scripts\qmt-rpyc-server.exe" (
    echo [ERROR] Run scripts\setup.bat for this checkout first.
    exit /b 1
)
"%~dp0.venv\Scripts\qmt-rpyc-server.exe" --config "%~dp0.env" start

:finished
set "START_EXIT=%errorlevel%"
pause
exit /b %START_EXIT%
