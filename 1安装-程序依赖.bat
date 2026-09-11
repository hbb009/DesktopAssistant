@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -U pip
python -m pip install -r requirements.txt
echo.
echo 完成。运行 3启动.bat
pause
