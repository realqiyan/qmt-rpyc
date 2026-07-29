@echo off
setlocal
chcp 65001 >nul 2>&1

set "QMT_RPYC_VERSION=0.3.0rc1"
set "QMT_RPYC_ROOT=%LOCALAPPDATA%\qmt-rpyc"
set "QMT_RPYC_VENV=%QMT_RPYC_ROOT%\venv"
set "PYTHON_CMD="

py -3.11 --version >nul 2>&1
if %errorlevel%==0 set "PYTHON_CMD=py -3.11"
if not defined PYTHON_CMD (
    py -3.10 --version >nul 2>&1
    if %errorlevel%==0 set "PYTHON_CMD=py -3.10"
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

"%QMT_RPYC_VENV%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
set "BUNDLED_WHEEL=%~dp0qmt_rpyc-%QMT_RPYC_VERSION%-py3-none-any.whl"
if exist "%BUNDLED_WHEEL%" (
    "%QMT_RPYC_VENV%\Scripts\python.exe" -m pip install "%BUNDLED_WHEEL%"
    if errorlevel 1 exit /b 1
    "%QMT_RPYC_VENV%\Scripts\python.exe" -m pip install "python-dotenv>=1" "numpy>=1.24,<2" "pandas>=2,<3" "psutil>=5"
) else (
    "%QMT_RPYC_VENV%\Scripts\python.exe" -m pip install --pre "qmt-rpyc[server]==%QMT_RPYC_VERSION%"
)
if errorlevel 1 exit /b 1

> "%QMT_RPYC_ROOT%\qmt-rpyc-server.bat" echo @echo off
>> "%QMT_RPYC_ROOT%\qmt-rpyc-server.bat" echo "%QMT_RPYC_VENV%\Scripts\qmt-rpyc-server.exe" %%*

echo.
echo qmt-rpyc %QMT_RPYC_VERSION% installed.
echo Launcher: %QMT_RPYC_ROOT%\qmt-rpyc-server.bat
echo.
call "%QMT_RPYC_ROOT%\qmt-rpyc-server.bat" init
if errorlevel 1 exit /b 1
call "%QMT_RPYC_ROOT%\qmt-rpyc-server.bat" check
exit /b %errorlevel%
