@echo off
chcp 65001 >nul
cd /d "%~dp0.."
python tools\download_models.py %*
pause
