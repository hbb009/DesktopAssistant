@echo off
setlocal
cd /d "%~dp0"
title DesktopAssistant v9.16 - Step3 Launch
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo.
echo ========================================
echo   Step 3/3  Launch app
echo   DesktopAssistant v9.16
echo ========================================
echo.
where python >nul 2>&1
if errorlevel 1 goto no_python
python tools\install_step3.py
set RC=%ERRORLEVEL%
echo.
echo Step 3 finished. Exit code=%RC%
echo.
pause
exit /b %RC%

:no_python
echo.
echo [FAIL] python not found in PATH.
echo Install Python 3.10+ and check "Add python.exe to PATH".
echo.
pause
exit /b 1
