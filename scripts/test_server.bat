@echo off
setlocal
REM ============================================================
REM qmt-rpyc Server Test Suite (Windows only)
REM
REM Runs ALL tests including server-side tests that require
REM numpy, pandas, and xtquant (QMT/MiniQMT).
REM
REM Prerequisites:
REM   - .venv created (run scripts\setup_dev.bat first)
REM   - MiniQMT running for live integration tests
REM
REM Usage:
REM   scripts\test_server.bat           Run all tests
REM   scripts\test_server.bat -k "not live"   Skip live tests
REM   scripts\test_server.bat -v -x            Verbose, stop on first failure
REM ============================================================

set VENV_PYTHON=.venv\Scripts\python.exe

if not exist "%VENV_PYTHON%" (
    echo [ERROR] .venv not found. Run scripts\setup_dev.bat first.
    exit /b 1
)

echo === qmt-rpyc server test suite ===
echo.

"%VENV_PYTHON%" -m pytest tests/ -v %*

endlocal
