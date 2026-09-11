@echo off
chcp 65001 >nul
echo ========================================
echo   Desktop Assistant - PaddleOCR-VL
echo ========================================
echo.
echo Python:
echo   "D:\Python311\python.exe"
echo.
echo Installing paddleocr (PaddleOCR-VL-1.6 runtime) ...
echo Close Desktop Assistant before running this.
echo GPU example uses CUDA 12.6. For CPU, install paddlepaddle==3.2.1 instead.
echo.
"D:\Python311\python.exe" -m pip install paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
if errorlevel 1 (
  echo paddlepaddle-gpu failed, trying CPU paddlepaddle ...
  "D:\Python311\python.exe" -m pip install paddlepaddle==3.2.1
)
"D:\Python311\python.exe" -m pip install -U "paddleocr[doc-parser]>=3.6.0"
echo.
if errorlevel 1 (
  echo FAILED. Try running this bat as Administrator,
  echo or close all python.exe in Task Manager first.
  pause
  exit /b 1
)
echo OK. Put weights in model\PaddleOCR-VL-1.6 then start Desktop Assistant.
pause
