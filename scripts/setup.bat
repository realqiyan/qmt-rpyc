@echo off
setlocal
cd /d "%~dp0.."

REM Source checkout setup; the release ZIP uses install-server.bat instead.
set "VENV_PYTHON=%CD%\.venv\Scripts\python.exe"
if exist "%VENV_PYTHON%" goto :validate_venv

py -3.11 -c "import struct; assert struct.calcsize('P') == 8" >nul 2>&1
if not errorlevel 1 (
    set "SETUP_PYTHON=py -3.11"
    goto :create_venv
)
py -3.10 -c "import struct; assert struct.calcsize('P') == 8" >nul 2>&1
if not errorlevel 1 (
    set "SETUP_PYTHON=py -3.10"
    goto :create_venv
)
python -c "import sys, struct; assert sys.version_info[:2] in [(3, 10), (3, 11)] and struct.calcsize('P') == 8" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Install 64-bit Python 3.10 or 3.11.
    exit /b 1
)
set "SETUP_PYTHON=python"

:create_venv
%SETUP_PYTHON% -m venv .venv
if errorlevel 1 exit /b 1

:validate_venv
"%VENV_PYTHON%" -c "import sys, struct; assert sys.version_info[:2] in [(3, 10), (3, 11)] and struct.calcsize('P') == 8"
if errorlevel 1 (
    echo [ERROR] Existing .venv must use 64-bit Python 3.10 or 3.11. Recreate it before continuing.
    exit /b 1
)

"%VENV_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%VENV_PYTHON%" -m pip install -e ".[server,dev]"
if errorlevel 1 exit /b 1

"%VENV_PYTHON%" -m qmt_rpyc.cli.server --config "%CD%\.env" init
if errorlevel 1 exit /b 1
"%VENV_PYTHON%" -m qmt_rpyc.cli.server --config "%CD%\.env" check
if errorlevel 1 (
    echo [ERROR] Environment installed, but checks failed. Fix the reported configuration or SDK issue and rerun check.
    exit /b 1
)
echo Setup complete. Run start-rpyc.bat to start this checkout.
exit /b 0
