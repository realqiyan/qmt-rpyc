@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo  qmt-rpyc — Server Environment Setup (Windows)
echo ============================================================
echo.

cd /d "%~dp0\.."

REM --- 1. check Python version -------------------------------------------------
echo [1/4] Checking Python version...

set PYTHON_EXE=
set PYTHON_VER=

REM prefer py launcher with explicit version
py -3.11 --version >nul 2>&1
if %errorlevel%==0 (
    set PYTHON_EXE=py -3.11
    for /f "tokens=2" %%v in ('py -3.11 --version 2^>^&1') do set PYTHON_VER=%%v
    goto :python_found
)

py -3.10 --version >nul 2>&1
if %errorlevel%==0 (
    set PYTHON_EXE=py -3.10
    for /f "tokens=2" %%v in ('py -3.10 --version 2^>^&1') do set PYTHON_VER=%%v
    goto :python_found
)

REM fallback to default python
python --version >nul 2>&1
if %errorlevel%==0 (
    set PYTHON_EXE=python
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYTHON_VER=%%v
) else (
    echo [ERROR] Python not found. Install Python 3.10 or 3.11.
    echo         https://www.python.org/downloads/
    pause
    exit /b 1
)

:python_found
echo         Found Python %PYTHON_VER%  (%PYTHON_EXE%)

REM verify major.minor is 3.10 or 3.11
for /f "tokens=1-2 delims=." %%a in ("%PYTHON_VER%") do (
    set MAJOR=%%a
    set MINOR=%%b
)
if not "!MAJOR!"=="3" (
    echo [ERROR] Python 3.10+ required, got %PYTHON_VER%
    pause
    exit /b 1
)
REM numeric comparison via subtraction to avoid string-compare pitfalls
set /a "_VER_GAP=!MINOR! - 10"
if !_VER_GAP! LSS 0 (
    echo [ERROR] Python 3.10+ required, got %PYTHON_VER%. xtquant supports 3.10-3.11 only.
    pause
    exit /b 1
)
set /a "_VER_GAP=!MINOR! - 11"
if !_VER_GAP! GTR 0 (
    echo [WARN]  Python %PYTHON_VER% detected. xtquant supports 3.10-3.11 only.
    echo         Consider installing Python 3.11 alongside.
)

REM --- 2. create venv ----------------------------------------------------------
echo.
echo [2/4] Creating virtual environment (.venv)...

if exist ".venv\Scripts\python.exe" (
    echo         .venv already exists, skipping.
) else (
    %PYTHON_EXE% -m venv .venv
    if !errorlevel! neq 0 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
    echo         Created .venv
)

set VENV_PYTHON=%CD%\.venv\Scripts\python.exe

REM --- 3. install dependencies -------------------------------------------------
echo.
echo [3/4] Installing server dependencies...

%VENV_PYTHON% -m pip install --upgrade pip -q
%VENV_PYTHON% -m pip install -r requirements-server.txt -q
if %errorlevel% neq 0 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)
echo         Done.

REM --- 4. environment self-check (auto-wires xtquant, configures .env) ---------
echo.
echo [4/4] Environment self-check (env_check.py)...

%VENV_PYTHON% scripts\env_check.py
if %errorlevel% neq 0 (
    echo.
    echo [WARN]  Some checks did not pass. See details above.
    echo         The venv and dependencies are ready — fix the issues above and re-run.
) else (
    echo.
    echo All checks passed.
)

REM --- done -------------------------------------------------------------------
echo.
echo ============================================================
echo  Setup complete.
echo.
echo  Next steps:
echo    1. If MiniQMT was not running: edit .env with your real values
echo       notepad .env
echo    2. Start the server
echo       start-rpyc.bat
echo ============================================================

endlocal
pause
