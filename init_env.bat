@echo off
REM ============================================================
REM  qmt-rpyc - Environment Init
REM  Steps: check/create venv -> upgrade pip -> install deps
REM ============================================================

cd /d "%~dp0"

echo ============================================================
echo  [1/4] Check virtual environment .venv
echo ============================================================
if exist ".venv\Scripts\python.exe" (
    echo  Found, skip creation.
) else (
    echo  Not found, creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo  [ERROR] venv creation failed. Make sure Python is installed and in PATH.
        goto :end
    )
    echo  Virtual environment created.
)

set "PY=.venv\Scripts\python.exe"

echo.
echo ============================================================
echo  [2/4] Upgrade pip
echo ============================================================
"%PY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo  [WARN] pip upgrade failed, continue.
)

echo.
echo ============================================================
echo  [3/4] Install dependencies (requirements-server.txt)
echo ============================================================
"%PY%" -m pip install -r requirements-server.txt
if errorlevel 1 (
    echo  [ERROR] Dependency install failed. Check requirements-server.txt or network.
    goto :end
)
echo  Dependencies installed.

echo.
echo ============================================================
echo  [4/4] Environment self-check (env_check.py)
echo ============================================================
echo  (detects MiniQMT process, auto-wires xtquant, checks account login)
"%PY%" env_check.py
if errorlevel 1 (
    echo.
    echo  [WARN] Self-check did not fully pass. Check details above.
    echo         Venv and dependencies are ready; you may proceed.
) else (
    echo.
    echo  Self-check passed.
)

echo.
echo ============================================================
echo  Init complete.
echo  - Full setup (xtquant wiring + .env): scripts\setup.bat
echo  - Start server:                        start-rpyc.bat
echo  - Run tests:                           .venv\Scripts\python.exe -m pytest tests/ -v
echo ============================================================

:end
echo.
pause
