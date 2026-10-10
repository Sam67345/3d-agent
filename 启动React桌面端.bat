@echo off
chcp 65001 >nul
set PYTHONUTF8=1
title SpatialZ 3D Agent - React Desktop
cd /d "%~dp0"

echo ============================================================
echo  SpatialZ 3D Agent - React desktop shell
echo    window : 1400x900 pywebview window (React UI)
echo    service: http://127.0.0.1:7861  (API + built frontend)
echo    Gradio : http://127.0.0.1:7860  (old version, run separately)
echo    Closing the window stops everything.
echo ============================================================
echo.

REM ?????? API + ????????? B?????? python ???
REM ?? --probe???????????????
".venv\Scripts\python.exe" "desktop_react.py"

echo.
echo Desktop shell exited.
pause >nul
