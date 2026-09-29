@echo off
setlocal
pushd "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "result=%ERRORLEVEL%"
echo.
if "%result%"=="0" (
  echo Installation complete.
) else (
  echo Installation failed. Read the error above, then rerun setup.cmd.
)
pause
popd
exit /b %result%
