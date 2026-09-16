@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 启动桌面助手 v9.16 ...
python mainv916.py
if errorlevel 1 pause
