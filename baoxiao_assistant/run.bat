@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo    🌾 青禾报销助手_qxy  启动脚本 (Windows)
echo ============================================

if not exist ".venv" (
    echo [1/3] 正在创建虚拟环境 .venv ...
    python -m venv .venv
)

echo [2/3] 激活虚拟环境并安装依赖 ...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q
if errorlevel 1 (
    echo 依赖安装失败，请检查网络连接或手动 pip install
    pause
    exit /b 1
)

echo [3/3] 启动服务 http://127.0.0.1:8000 ...
echo 浏览器打开后默认进入登录页，可选员工 / 行政 / 财务账号。
echo.
uvicorn main:app --host 127.0.0.1 --port 8000 --reload