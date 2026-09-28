@echo off
setlocal
set "QMT_RPYC_ADAPTER=bigqmt"
call "%~dp0start-rpyc.bat" %*
exit /b %ERRORLEVEL%
