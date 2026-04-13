@echo off
chcp 65001 >nul
title Meme Trader
echo === Meme Trader 启动 ===

:: 先杀掉所有旧的 Python main.py 进程（防止多进程同时买入）
echo 清理旧进程...
for /f "tokens=2" %%i in ('tasklist /fi "imagename eq python.exe" /fo csv /nh 2^>nul') do (
    wmic process where "ProcessId=%%~i" get CommandLine 2>nul | findstr "main.py" >nul 2>&1
    if not errorlevel 1 (
        taskkill /PID %%~i /F >nul 2>&1
    )
)
timeout /t 1 /nobreak >nul

:: 找一个可用端口（从8000开始，跳过被占用的）
set BACKEND_PORT=8000
:check_port
netstat -ano | findstr ":%BACKEND_PORT% " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    set /a BACKEND_PORT+=1
    goto check_port
)
echo 后端将使用端口: %BACKEND_PORT%

:: 启动后端
echo 启动后端...
start "Meme Trader Backend" cmd /k "cd /d "%~dp0backend" && set BACKEND_PORT=%BACKEND_PORT% && python main.py"

:: 等待后端启动
timeout /t 5 /nobreak >nul

:: 启动前端（传入后端端口，vite.config.js 会读取）
echo 启动前端...
start "Meme Trader Frontend" cmd /k "cd /d "%~dp0frontend" && set BACKEND_PORT=%BACKEND_PORT% && npm run dev -- --port 5173"

echo.
echo 启动完成！
echo   前端: http://localhost:5173
echo   后端: http://localhost:%BACKEND_PORT%
echo.
timeout /t 3 /nobreak >nul
start http://localhost:5173
