@echo off
rem Double-click launcher for publish.ps1 (sync spec PV-SPEC-2026-10-v16 / 16.0).
rem Put a shortcut to this file on the desktop and name it as you like.
rem Keep this file ASCII only: publish.ps1 switches the console to UTF-8,
rem and cmd.exe reads the rest of this file with that code page.
title photo-viewer - publish
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish.ps1" %*
set "PV_EXIT=%ERRORLEVEL%"
echo.
if "%PV_EXIT%"=="0" goto done
if "%PV_EXIT%"=="2" goto stopped
if "%PV_EXIT%"=="75" goto busy
color 4F
echo  FAILED (exit %PV_EXIT%) - nothing was published. Read the red lines above.
goto finish
:done
color 2F
echo  OK (exit 0) - finished. If photos changed, the site updates in 1-2 minutes.
goto finish
:stopped
color 6F
echo  STOPPED (exit 2) - a safety check stopped publishing. Read the lines above.
goto finish
:busy
color 6F
echo  BUSY (exit 75) - another publish is already running. Try again later.
:finish
echo.
pause
exit /b %PV_EXIT%
