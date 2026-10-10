@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
call "%~dp0build.bat" %*
exit /b %ERRORLEVEL%
