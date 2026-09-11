@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 请先按 requirements-models.txt 注释安装 paddlepaddle / torch（如需要）
pause
python -m pip install -r requirements-models.txt
python tools\download_models.py
pause
