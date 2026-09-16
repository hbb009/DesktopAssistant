@echo off
title 桌面助手 v9.16 - 2安装模型
cd /d "%~dp0"

echo ========================================
echo   2/3  安装模型（写入 model\）
echo   桌面助手 v9.16
echo ========================================
echo.
echo model\ 下均为开源模型，例如：
echo   PaddleOCR-VL-1.6
echo   faster-whisper（语音转写）
echo   Fun-CosyVoice3 / CosyVoice（语音克隆）
echo.
echo model\ 不进 Git。
echo 若你已整包拷贝了 model\，可跳过本步。
echo.
echo ----------------------------------------
echo 按任意键打开下载菜单...
pause >nul
echo.

echo [0/3] 检查 Python ...
where python >nul 2>&1
if errorlevel 1 goto no_python
python --version
echo [成功] 已找到 Python
echo.

echo [1/3] 确认 model\ 目录 ...
if not exist "model" mkdir model
echo [成功] %CD%\model
echo.

echo [2/3] 启动下载菜单 ...
echo 已存在的模型会自动跳过。
echo 国内网络较慢时可另开窗口用：
echo   python tools\download_models.py --mirror modelscope
echo.
python tools\download_models.py %*
set DL=%ERRORLEVEL%
echo.
if not "%DL%"=="0" goto fail_dl

echo [3/3] 第 2 步结束
echo ----------------------------------------
echo 下一步：双击运行  3启动-程序.bat
echo ----------------------------------------
echo.
echo 按任意键关闭本窗口...
pause >nul
exit /b 0

:no_python
echo.
echo [失败] 找不到 python。请先完成 1安装-组件.bat。
echo.
echo 按任意键关闭...
pause >nul
exit /b 1

:fail_dl
echo [失败] 下载中断，退出码=%DL%
echo 可重试本 bat，或：
echo   python tools\download_models.py --only paddleocr
echo   python tools\download_models.py --all-light --mirror modelscope
echo.
echo 按任意键关闭...
pause >nul
exit /b 1
