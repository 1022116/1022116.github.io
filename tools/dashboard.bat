@echo off
rem Double-click launcher for the dashboard (sync spec PV-SPEC-2026-10-v18 / 18.0).
rem Reads the latest numbers from GoatCounter and the publish logs, writes
rem dashboard\index.html (on this PC only; it is never published) and opens it.
rem Put a shortcut to this file on the desktop and name it as you like. Keep this file ASCII only.
title photo-viewer - dashboard
cd /d "%~dp0.."
set "PV_PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PV_PY%" (
  echo  The virtual environment ^(.venv^) was not found. Follow the first-time setup in README.md.
  pause
  exit /b 1
)
set PYTHONUTF8=1
"%PV_PY%" "%~dp0make_dashboard.py" --open %*
set "PV_EXIT=%ERRORLEVEL%"
if not "%PV_EXIT%"=="0" (
  echo.
  echo  The dashboard could not be made ^(exit %PV_EXIT%^). Read the lines above.
  pause
)
exit /b %PV_EXIT%
