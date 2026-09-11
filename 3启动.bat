@echo off
chcp 65001 >nul
cd /d "%~dp0"
python mainv916.py
if errorlevel 1 pause
