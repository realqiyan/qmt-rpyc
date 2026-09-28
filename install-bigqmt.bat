@echo off
setlocal
set "QMT_RPYC_ADAPTER=bigqmt"
call "%~dp0install-server.bat" %*
set "INSTALL_EXIT=%ERRORLEVEL%"
echo.
echo After installation succeeds: stop the old QMT bridge strategy, replace it with this bundle's bigqmt_strategy.py,
echo then start the strategy in QMT and run start-bigqmt.bat.
echo Installing the external service does not replace the strategy inside QMT.
exit /b %INSTALL_EXIT%
