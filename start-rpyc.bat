@echo off
chcp 65001 >nul 2>&1
set "MANAGED=%LOCALAPPDATA%\qmt-rpyc\qmt-rpyc-server.bat"

if exist "%MANAGED%" (
    call "%MANAGED%" start
) else if exist "%~dp0.venv\Scripts\qmt-rpyc-server.exe" (
    "%~dp0.venv\Scripts\qmt-rpyc-server.exe" --config "%~dp0.env" start
) else (
    echo [ERROR] qmt-rpyc server is not installed.
    echo Run install-server.bat or scripts\setup.bat first.
    pause
    exit /b 1
)
pause
