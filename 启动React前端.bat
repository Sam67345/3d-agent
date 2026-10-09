@echo off
chcp 65001 >nul
title SpatialZ 3D Agent - React 前端 + API
cd /d "%~dp0"

echo ============================================================
echo  启动 SpatialZ 3D Agent（React 前端版）
echo   API 服务 : http://127.0.0.1:7861   （后端，勿关）
echo   前端界面 : http://127.0.0.1:3001   （浏览器会自动打开）
echo   Gradio 旧版: http://127.0.0.1:7860  （如需请另开窗口跑 python web_app.py）
echo   关闭本窗口即结束全部进程
echo ============================================================
echo.

REM 1) 先起 API（pythonw 无黑框，日志写到 output/api_server.log）
start "SpatialZ API" /min cmd /c ^
  ".venv\Scripts\pythonw.exe api_server.py > output\api_server.log 2>&1"

REM 2) 等 API 端口就绪（最多 40 秒）
set /a WAITED=0
:WAIT_API
timeout /t 2 >nul
set /a WAITED+=2
powershell -NoProfile -Command ^
  "if (Test-NetConnection -ComputerName 127.0.0.1 -Port 7861 -InformationLevel Quiet -WarningAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
if %WAITED% lss 40 goto WAIT_API
echo [警告] API 未在 40 秒内就绪，请检查 output\api_server.log
echo.

REM 3) 起前端 dev server（npm 会常驻，关窗口随本脚本一起结束）
start "SpatialZ React Dev" cmd /c "cd /d "%~dp0frontend" && npm run dev"

echo.
echo 前端启动中… 首次启动可能需要十几秒编译。
echo 浏览器访问：http://127.0.0.1:3001
echo.

REM 4) 打开浏览器；本窗口保持，关掉即结束
start "" http://127.0.0.1:3001
echo 按任意键结束本窗口（同时结束 API 与前端进程）...
pause >nul

echo 正在关闭…
taskkill /f /fi "WINDOWTITLE eq SpatialZ API*" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq SpatialZ React Dev*" >nul 2>&1
echo 已退出。