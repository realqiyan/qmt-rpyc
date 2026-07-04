@echo off
setlocal

echo ============================================================
echo  qmt-rpyc — Dev Environment Setup (Windows)
echo ============================================================
echo.

cd /d "%~dp0\.."

REM --- check .venv exists -----------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [WARN]  .venv not found. Run scripts\setup.bat first.
    pause
    exit /b 1
)

set VENV_PYTHON=%CD%\.venv\Scripts\python.exe

REM --- install dev dependencies -----------------------------------------------
echo [1/2] Installing dev dependencies...
%VENV_PYTHON% -m pip install -r requirements-dev.txt -q
if %errorlevel% neq 0 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)
echo         Done.

REM --- install client package in dev mode -------------------------------------
echo [2/2] Installing qmt-rpyc-client in dev mode...
%VENV_PYTHON% -m pip install -e . -q
echo         Done.

REM --- done -------------------------------------------------------------------
echo.
echo ============================================================
echo  Dev setup complete.
echo.
echo  Run tests:
echo    .venv\Scripts\python.exe -m pytest tests/ -v
echo ============================================================

endlocal
pause
