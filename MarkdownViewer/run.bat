@echo off
setlocal
set "SCRIPT=%~dp0reader.pyw"
where pythonw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pythonw.exe "%SCRIPT%" %*
    exit /b
)
where pyw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pyw.exe -3 "%SCRIPT%" %*
    exit /b
)
start "" python.exe "%SCRIPT%" %*
