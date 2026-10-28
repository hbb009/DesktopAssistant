@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 goto no_python
python -c "import PyQt5.QtWidgets, psutil, requests" >nul 2>&1
if errorlevel 1 goto no_deps
if not exist "mainv918.py" goto no_main
start "" pythonw "%~dp0mainv918.py"
exit /b 0

:no_python
echo [FAIL] python not found in PATH. Install Python 3.10/3.11, then run step 1 bat (1*.bat).
pause
exit /b 1

:no_deps
echo [FAIL] Core packages missing. Run step 1 bat (1*.bat) first. Details:
python -c "import PyQt5.QtWidgets, psutil, requests"
pause
exit /b 1

:no_main
echo [FAIL] mainv918.py not found in %~dp0
pause
exit /b 1
