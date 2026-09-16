@echo off
title 桌面助手 v9.16
cd /d "%~dp0"

echo ========================================
echo   3/3  启动程序
echo   桌面助手 v9.16
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 goto no_python

echo 工作目录：%CD%
echo 正在启动 mainv916.py ...
echo ----------------------------------------
echo.
python mainv916.py
set RC=%ERRORLEVEL%
echo.
echo ----------------------------------------
if not "%RC%"=="0" (
  echo [失败] 程序退出码=%RC%
  echo 可查看 data\app.log（若已生成）。
  echo.
  echo 按任意键关闭...
  pause >nul
  exit /b %RC%
)

echo [成功] 程序已正常退出。
echo.
echo 按任意键关闭...
pause >nul
exit /b 0

:no_python
echo [失败] 找不到 python。
echo 请先运行 1安装-组件.bat。
echo.
echo 按任意键关闭...
pause >nul
exit /b 1
