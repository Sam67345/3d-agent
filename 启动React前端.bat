@echo off
title SpatialZ 3D Agent - React Frontend
cd /d "%~dp0"

echo ============================================================
echo  SpatialZ 3D Agent (React frontend)
echo    API   : http://127.0.0.1:7861
echo    WEB UI: http://127.0.0.1:3001
echo    Gradio: http://127.0.0.1:7860  (old version, run separately)
echo    Close this window = stop API + frontend
echo ============================================================
echo.

REM ---------- 1) start API (new window, kept open with /k so errors stay visible) ----------
if not exist "output" mkdir "output"
start "SpatialZ API" /min cmd /k .venv\Scripts\python.exe api_server.py
echo [1/3] API starting...

REM ---------- 2) wait for API port (max 60s) ----------
set /a WAITED=0
:WAIT_API
ping -n 3 127.0.0.1 >nul
set /a WAITED+=2
powershell -NoProfile -Command "if (Test-NetConnection -ComputerName 127.0.0.1 -Port 7861 -InformationLevel Quiet -WarningAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
if %WAITED% lss 60 goto WAIT_API
echo [2/3] API ready (waited %WAITED%s)

REM ---------- 3) start frontend dev server ----------
REM use start /D to set the working dir: no nested quotes (nested quotes in start
REM get split by cmd and the window closes instantly)
/k keeps the window open so npm errors stay visible
start "SpatialZ React Dev" /D "%~dp0frontend" cmd /k npm run dev
echo [3/3] Frontend starting (first run takes ~15s to compile)
echo.

start "" http://127.0.0.1:3001

echo.
echo Web UI: http://127.0.0.1:3001
echo If the page does not open, check these two windows for errors:
echo   - "SpatialZ API"
echo   - "SpatialZ React Dev"
echo.
echo Press any key to stop API + frontend and exit...
pause >nul

echo stopping...
taskkill /f /fi "WINDOWTITLE eq SpatialZ API*" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq SpatialZ React Dev*" >nul 2>&1
echo bye.
