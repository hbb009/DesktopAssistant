@echo off
title 桌面助手 - OCR 修复
cd /d "%~dp0.."

echo ========================================
echo   截图 OCR 修复（PaddleOCR-VL-1.6）
echo ========================================
echo.
echo 请先完全退出桌面助手再运行。
echo 本脚本会安装/修复：
echo   paddlepaddle（先试 GPU cu126，失败改 CPU）
echo   paddleocr[doc-parser]
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [失败] 找不到 python。
  pause
  exit /b 1
)

python --version
echo.
echo [1/2] 安装 paddlepaddle ...
python -m pip install paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
if errorlevel 1 (
  echo GPU 版失败，改装 CPU 版 ...
  python -m pip uninstall -y paddlepaddle-gpu paddlepaddle
  python -m pip install paddlepaddle==3.2.1
  if errorlevel 1 (
    echo [失败] paddlepaddle 安装失败。
    pause
    exit /b 1
  )
)

echo.
echo [2/2] 安装 paddleocr[doc-parser] ...
python -m pip install -U "paddleocr[doc-parser]>=3.6.0"
if errorlevel 1 (
  echo [失败] paddleocr 安装失败。
  echo 可再试：python tools\setup_components.py --only paddleocr --yes
  pause
  exit /b 1
)

echo.
echo [成功] 运行库已装。请确认权重在 model\PaddleOCR-VL-1.6
echo 真测：python tools\diagnose_dll.py ocr
echo.
pause
exit /b 0
