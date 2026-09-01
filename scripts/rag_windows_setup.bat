@echo off
setlocal

REM Double-click launcher for the Windows setup script.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0rag_windows_setup.ps1"
set "SETUP_EXIT_CODE=%ERRORLEVEL%"

if not "%SETUP_EXIT_CODE%"=="0" (
  echo.
  echo Setup did not complete. Review the message above, then try again.
  pause
  exit /b %SETUP_EXIT_CODE%
)

pause
exit /b 0
