@echo off
setlocal
REM ============================================================
REM qmt-rpyc Server Test Suite (Windows only)
REM
REM Runs the full suite using a synthetic SDK; no local QMT is required.
REM
REM Prerequisites:
REM   - .venv created (run scripts\setup.bat first)
REM   - Live tests require QMT_RPYC_LIVE=1 and a configured client profile
REM     selected by QMT_RPYC_PROFILE (default: default).
REM
REM Usage:
REM   scripts\test_server.bat           Run all tests
REM   scripts\test_server.bat -k "not live"   Skip live tests
REM   scripts\test_server.bat -v -x            Verbose, stop on first failure
REM ============================================================

cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run scripts\setup.bat first.
    exit /b 1
)

set "VENV_PYTHON=%CD%\.venv\Scripts\python.exe"

echo === qmt-rpyc server test suite ===
echo.

"%VENV_PYTHON%" -m pytest tests/ -v %*

exit /b %errorlevel%
