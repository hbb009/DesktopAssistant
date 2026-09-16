@echo off
setlocal
cd /d "%~dp0"
title DesktopAssistant v9.16 - Step1 Components
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo.
echo ========================================
echo   Step 1/3  Install components
echo   DesktopAssistant v9.16
echo ========================================
echo.
where python >nul 2>&1
if errorlevel 1 goto no_python
python --version
if errorlevel 1 goto no_python
echo.
python tools\install_step1.py
set RC=%ERRORLEVEL%
echo.
echo Step 1 finished. Exit code=%RC%
echo.
pause
exit /b %RC%

:no_python
echo.
echo [FAIL] python not found in PATH.
echo Install Python 3.10+ and check "Add python.exe to PATH".
echo Then open a NEW cmd window and run this bat again.
echo.
pause
exit /b 1
