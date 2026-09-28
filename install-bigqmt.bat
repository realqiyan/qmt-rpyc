@echo off
setlocal
set "QMT_RPYC_ADAPTER=bigqmt"
call "%~dp0install-server.bat" %*
exit /b %ERRORLEVEL%
