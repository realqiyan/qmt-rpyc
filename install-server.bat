@echo off
setlocal
chcp 65001 >nul 2>&1

set "QMT_RPYC_ROOT=%LOCALAPPDATA%\qmt-rpyc"
set "QMT_RPYC_VENV=%QMT_RPYC_ROOT%\venv"
set "PYTHON_CMD="

REM Require exactly one bundled wheel; never fetch a different bridge version.
set "BUNDLED_WHEEL="
for %%F in ("%~dp0qmt_rpyc-*-py3-none-any.whl") do (
    if exist "%%~fF" (
        if defined BUNDLED_WHEEL (
            echo [ERROR] Multiple qmt-rpyc wheels found. Extract a clean bundle.
            exit /b 1
        )
        set "BUNDLED_WHEEL=%%~fF"
    )
)
if not defined BUNDLED_WHEEL (
    echo [ERROR] Bundled qmt-rpyc wheel not found. Extract the complete Windows ZIP.
    exit /b 1
)

py -3.11 --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3.11"
if not defined PYTHON_CMD (
    py -3.10 --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3.10"
)
if not defined PYTHON_CMD (
    echo [ERROR] Python 3.10 or 3.11 is required.
    echo Download: https://www.python.org/downloads/windows/
    exit /b 1
)

if not exist "%QMT_RPYC_VENV%\Scripts\python.exe" (
    echo Creating managed environment at %QMT_RPYC_VENV%
    %PYTHON_CMD% -m venv "%QMT_RPYC_VENV%"
    if errorlevel 1 exit /b 1
)

"%QMT_RPYC_VENV%\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2] in ((3, 10), (3, 11)) and sys.maxsize > 2**32 else 1)"
if errorlevel 1 (
    echo [ERROR] The managed environment requires 64-bit Python 3.10 or 3.11.
    echo Environment: %QMT_RPYC_VENV%
    exit /b 1
)

"%QMT_RPYC_VENV%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
REM Reinstall the exact local build even if this development version exists.
"%QMT_RPYC_VENV%\Scripts\python.exe" -m pip install --force-reinstall --no-deps "%BUNDLED_WHEEL%"
if errorlevel 1 exit /b 1
"%QMT_RPYC_VENV%\Scripts\python.exe" -m pip install "%BUNDLED_WHEEL%[server]"
if errorlevel 1 exit /b 1

> "%QMT_RPYC_ROOT%\qmt-rpyc-server.bat" echo @echo off
>> "%QMT_RPYC_ROOT%\qmt-rpyc-server.bat" echo "%QMT_RPYC_VENV%\Scripts\qmt-rpyc-server.exe" %%*

echo.
"%QMT_RPYC_VENV%\Scripts\qmt-rpyc-server.exe" --version
echo Installed from: %BUNDLED_WHEEL%
echo Launcher: %QMT_RPYC_ROOT%\qmt-rpyc-server.bat
echo.
call "%QMT_RPYC_ROOT%\qmt-rpyc-server.bat" init
if errorlevel 1 exit /b 1
call "%QMT_RPYC_ROOT%\qmt-rpyc-server.bat" check
exit /b %errorlevel%
