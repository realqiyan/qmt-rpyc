@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo  qmt-rpyc — Server Environment Setup (Windows)
echo ============================================================
echo.

cd /d "%~dp0\.."

REM --- 1. check Python version -------------------------------------------------
echo [1/5] Checking Python version...

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
echo [2/5] Creating virtual environment (.venv)...

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

REM --- 3. install dependencies ------------------------------------------------
echo.
echo [3/5] Installing server dependencies...

%VENV_PYTHON% -m pip install --upgrade pip -q
%VENV_PYTHON% -m pip install -r requirements-server.txt -q
if %errorlevel% neq 0 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)
echo         Done.

REM --- 4. wire xtquant ---------------------------------------------------------
echo.
echo [4/5] Wiring xtquant from QMT install...

REM try QMT_PATH from .env first, validate, ask if needed
set QMT_PATH=
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if "%%a"=="QMT_PATH" set QMT_PATH=%%b
    )
)

:prompt_qmt_path
REM validate the path: does QMT's xtquant actually exist there?
set QMT_VALID=0
if not "!QMT_PATH!"=="" (
    set "QMT_SITE=!QMT_PATH!\..\bin.x64\Lib\site-packages"
    if exist "!QMT_SITE!\xtquant\" set QMT_VALID=1
    if exist "!QMT_SITE!\xtdata.py" set QMT_VALID=1
)

if "!QMT_VALID!"=="1" goto :qmt_path_ok

REM path is missing or invalid — prompt user
echo.
if "!QMT_PATH!"=="" (
    echo         Enter your QMT/MiniQMT userdata directory path.
) else (
    echo [WARN]  xtquant not found at !QMT_PATH!
    echo         Enter the correct QMT/MiniQMT userdata directory path.
)
echo         Example: D:\ACT\userdata_mini
echo.
set /p QMT_PATH="         QMT_PATH = "
if "!QMT_PATH!"=="" (
    echo [WARN]  No path entered, skipping xtquant wiring.
    goto :skip_xtquant
)
goto :prompt_qmt_path

:qmt_path_ok
echo         QMT_PATH = !QMT_PATH!
%VENV_PYTHON% scripts\install_xtquant.py "!QMT_PATH!"
if !errorlevel! neq 0 (
    echo [ERROR] xtquant wiring failed.
) else (
    echo         Done.
)

:skip_xtquant
REM nothing to do here

REM --- 5. create .env ---------------------------------------------------------
echo.
echo [5/5] Preparing .env configuration...

if not exist ".env" (
    copy .env.example .env >nul
    echo         Created .env from .env.example — edit it with your real values.
) else (
    echo         .env already exists, skipping.
)

REM --- done -------------------------------------------------------------------
echo.
echo ============================================================
echo  Setup complete.
echo.
echo  Next steps:
echo    1. Edit .env with your real values
echo       notepad .env
echo    2. Start the server
echo       start_server.bat
echo ============================================================

endlocal
pause
