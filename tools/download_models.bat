@echo off
setlocal
cd /d "%~dp0.."
title DesktopAssistant - download models
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo Download open-source models into model\
echo.
where python >nul 2>&1
if errorlevel 1 (
  echo [FAIL] python not found in PATH.
  pause
  exit /b 1
)
python tools\download_models.py %*
echo.
pause
