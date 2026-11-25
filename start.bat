@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"

echo ============================================
echo   A4S - Agent For Students  (v0.1)
echo ============================================
echo.

if not exist "%ROOT%backend\.venv\Scripts\python.exe" (
  echo [错误] 未找到后端虚拟环境。
  echo 请先执行：
  echo    cd backend
  echo    python -m venv .venv
  echo    .venv\Scripts\python.exe -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

if not exist "%ROOT%frontend\node_modules" (
  echo [提示] 前端依赖未安装，正在安装...
  pushd "%ROOT%frontend"
  call npm install --no-audit --no-fund
  popd
)

if not exist "%ROOT%.env" (
  echo [提示] 未找到 .env，已从 .env.example 创建。请按需填写 API Key。
  copy "%ROOT%.env.example" "%ROOT%.env" >nul
)

echo [1/3] 启动后端 http://127.0.0.1:8000
start "A4S Backend" cmd /k "cd /d %ROOT%backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"

echo [2/3] 启动前端 http://localhost:5173
start "A4S Frontend" cmd /k "cd /d %ROOT%frontend && npm run dev"

echo [3/3] 等待服务就绪并打开浏览器...
timeout /t 5 >nul
start "" http://localhost:5173

echo.
echo 已启动。关闭两个命令行窗口即可停止服务。
endlocal
