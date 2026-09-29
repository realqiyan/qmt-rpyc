@echo off
setlocal
chcp 65001 >nul 2>&1

set "QMT_RPYC_ROOT=%LOCALAPPDATA%\qmt-rpyc"
set "QMT_RPYC_VENV=%QMT_RPYC_ROOT%\venv"
set "PYTHON_CMD="
REM Keep the online installer pin aligned with the release version.
set "QMT_RPYC_VERSION=0.8.1"
set "QMT_RPYC_INDEX=https://pypi.org/simple"
if not "%QMT_RPYC_VERSION:rc=%"=="%QMT_RPYC_VERSION%" set "QMT_RPYC_INDEX=https://test.pypi.org/simple"
set "QMT_RPYC_DOWNLOAD=%TEMP%\qmt-rpyc-bootstrap-%RANDOM%-%RANDOM%"

REM Existing installations are maintained through the installed update command.
if exist "%QMT_RPYC_VENV%\Scripts\qmt-rpyc-server.exe" (
    echo Already installed. Use the installed commands for maintenance:
    echo "%QMT_RPYC_VENV%\Scripts\qmt-rpyc-server.exe" update
    exit /b 1
)

REM Prefer the bundled acceptance/release wheel; otherwise download the pinned release.
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
if defined BUNDLED_WHEEL (
    set "INSTALL_SOURCE=%BUNDLED_WHEEL%"
) else (
    set "INSTALL_SOURCE=%QMT_RPYC_INDEX% qmt-rpyc==%QMT_RPYC_VERSION%"
)
echo Installing from: %INSTALL_SOURCE%
echo First installation only. Existing configuration will be preserved.

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
if defined BUNDLED_WHEEL (
    "%QMT_RPYC_VENV%\Scripts\python.exe" -m pip install --index-url https://pypi.org/simple "%BUNDLED_WHEEL%[server]"
    if errorlevel 1 exit /b 1
) else (
    REM Fetch only qmt-rpyc from its release channel; dependencies use production PyPI.
    "%QMT_RPYC_VENV%\Scripts\python.exe" -m pip download --no-deps --only-binary=:all: --index-url "%QMT_RPYC_INDEX%" --dest "%QMT_RPYC_DOWNLOAD%" "qmt-rpyc==%QMT_RPYC_VERSION%"
    if errorlevel 1 exit /b 1
    "%QMT_RPYC_VENV%\Scripts\python.exe" -m pip install --index-url https://pypi.org/simple "%QMT_RPYC_DOWNLOAD%\qmt_rpyc-%QMT_RPYC_VERSION%-py3-none-any.whl[server]"
    if errorlevel 1 exit /b 1
    rmdir /s /q "%QMT_RPYC_DOWNLOAD%"
)

"%QMT_RPYC_VENV%\Scripts\python.exe" -m pip check
if errorlevel 1 exit /b 1
"%QMT_RPYC_VENV%\Scripts\python.exe" "%~dp0verify-install.py" --expected-version "%QMT_RPYC_VERSION%"
if errorlevel 1 (
    echo [ERROR] Installation verification failed. Extract the current official bundle and reinstall.
    exit /b 1
)

"%QMT_RPYC_VENV%\Scripts\qmt-rpyc-server.exe" init %*
if errorlevel 1 exit /b 1
echo.
echo Installation complete. Next commands:
echo "%QMT_RPYC_VENV%\Scripts\qmt-rpyc-server.exe" check
echo "%QMT_RPYC_VENV%\Scripts\qmt-rpyc-server.exe" start
echo For later upgrades, use the same executable with update.
echo BigQMT: separately install the matching GBK strategy inside QMT.
exit /b 0
