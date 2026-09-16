@echo off
title 桌面助手 - 下载模型
cd /d "%~dp0.."
echo 下载开源模型到 model\
echo.
where python >nul 2>&1
if errorlevel 1 (
  echo [失败] 找不到 python。
  pause
  exit /b 1
)
python tools\download_models.py %*
echo.
pause
