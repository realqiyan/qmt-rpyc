@echo off
chcp 65001 >nul 2>&1
REM ============================================================
REM  Start qmt-rpyc RPC server
REM  Config lives in .env (copy .env.example -> .env)
REM  Env vars override .env values.
REM ============================================================

cd /d "%~dp0"

REM --- check .venv ---------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run scripts\setup.bat first.
    pause
    exit /b 1
)

set "VENV_PYTHON=%CD%\.venv\Scripts\python.exe"

REM --- check .env ----------------------------------------------------------
if not exist ".env" (
    echo [WARN]  .env not found - copying .env.example as starter
    echo         Edit .env with your real values, then re-run.
    copy .env.example .env >nul
    pause
    exit /b 1
)

REM --- ensure log dir exists -----------------------------------------------
if not exist "logs" mkdir logs

REM --- start ---------------------------------------------------------------
"%VENV_PYTHON%" -m server.main
pause
