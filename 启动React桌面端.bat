@echo off
chcp 65001 >nul
set PYTHONUTF8=1
title SpatialZ 3D Agent - React Desktop
cd /d "%~dp0"

REM Use pythonw.exe: no console window, only the pywebview UI window shows.
REM Logs: output\desktop_react.log  (fatal errors also pop up a MessageBox)
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "desktop_react.py"
) else (
  start "" ".venv\Scripts\python.exe" "desktop_react.py"
)
exit