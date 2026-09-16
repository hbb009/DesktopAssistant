@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo  1/3  安装组件（程序依赖 + 运行库）
echo ========================================
echo.
echo 将安装：requirements.txt、ffmpeg / torch / paddle /
echo paddleocr[doc-parser] / CosyVoice 源码与相关依赖等。
echo 不含 model\ 大模型权重（请接着跑 2安装-模型.bat）。
echo.

python -m pip install -U pip
if errorlevel 1 goto fail
python -m pip install -r requirements.txt
if errorlevel 1 goto fail

echo.
echo --- 组件脚本（按本机 CUDA 选轮子，可自动确认）---
python tools\setup_components.py --yes --only ffmpeg --only deps --only torch --only paddle --only paddleocr --only cosyvoice-code --only cosyvoice-deps
if errorlevel 1 (
  echo.
  echo [提示] 部分组件未装全。可再运行本脚本，或：
  echo   python tools\setup_components.py --check
  echo   python tools\setup_components.py
)

echo.
echo --- 体检 ---
python tools\setup_components.py --check

echo.
echo 完成。下一步：运行 2安装-模型.bat
echo （装过 ffmpeg 请新开一个命令行窗口再启动程序）
pause
exit /b 0

:fail
echo.
echo [失败] 组件安装中断。
pause
exit /b 1
