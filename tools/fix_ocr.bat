@echo off
setlocal
cd /d "%~dp0.."
title DesktopAssistant - OCR fix
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo.
echo ========================================
echo   OCR fix  PaddleOCR-VL
echo ========================================
echo Exit the app completely before running.
echo.
where python >nul 2>&1
if errorlevel 1 goto no_python
python --version
echo.
echo [1/2] install paddlepaddle ...
python -m pip install paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
if errorlevel 1 (
  echo GPU wheel failed, trying CPU ...
  python -m pip uninstall -y paddlepaddle-gpu paddlepaddle
  python -m pip install paddlepaddle==3.2.1
  if errorlevel 1 goto fail_paddle
)
echo.
echo [2/2] install paddleocr[doc-parser] ...
python -m pip install "paddleocr[doc-parser]>=3.6.0"
if errorlevel 1 goto fail_ocr
echo.
echo Done. Optional check:
python tools\setup_components.py --check
echo.
pause
exit /b 0

:fail_paddle
echo [FAIL] paddlepaddle install failed.
pause
exit /b 1

:fail_ocr
echo [FAIL] paddleocr install failed.
pause
exit /b 1

:no_python
echo [FAIL] python not found in PATH.
pause
exit /b 1
