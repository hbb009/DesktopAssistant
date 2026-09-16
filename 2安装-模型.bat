@echo off
title 桌面助手 v9.16 - 2安装模型
cd /d "%~dp0"

echo ========================================
echo   2/3  安装模型（写入 model\）
echo   桌面助手 v9.16
echo ========================================
echo.
echo 对应旧流程：2安装-模型依赖.bat 里的「下载权重」部分。
echo （模型相关 pip 已在第 1 步通过 deps / requirements-models 安装）
echo.
echo model\ 下均为开源内容，下载清单：
echo   [1] paddleocr      PaddleOCR-VL-1.6      约 1.9GB  截图OCR
echo   [2] whisper-tiny   faster-whisper-tiny           语音转写(小)
echo   [3] whisper-small  faster-whisper-small          推荐起步
echo   [4] whisper-large  faster-whisper-large-v3 约2.9GB
echo   [5] cosyvoice      Fun-CosyVoice3-0.5B     约 9GB  语音克隆
echo   [6] cosyvoice-code CosyVoice 源码
echo       （第1步一般已装；缺了可在菜单再下）
echo.
echo 另：图片放大可选 upscayl（upscayl-bin + 模型）
echo     请自行放到 model\upscayl\（下载菜单暂无此项）
echo.
echo 已存在的目录会跳过，不会覆盖。
echo 若整包已拷贝 model\，可直接选 q 退出菜单。
echo.
echo 国内网络可用镜像（另开窗口）：
echo   python tools\download_models.py --mirror modelscope
echo   python tools\download_models.py --all-light --mirror modelscope
echo.
echo ----------------------------------------
echo 按任意键打开下载菜单...
pause >nul
echo.

echo [1/4] 检查 Python ...
where python >nul 2>&1
if errorlevel 1 goto no_python
python --version
echo [成功] Python 可用
echo.

echo [2/4] 确认 model\ ...
if not exist "model" mkdir model
echo [成功] %CD%\model
echo.

echo [3/4] 当前已有模型：
python tools\download_models.py --list
echo.

echo [4/4] 打开下载菜单（对应旧2的 download_models）...
python tools\download_models.py %*
set DL=%ERRORLEVEL%
echo.

echo -------- 下载后复核 --------
python tools\download_models.py --list
echo.
if not "%DL%"=="0" goto fail_dl

echo [成功] 第 2 步结束。
echo 下一步：双击  3启动-程序.bat
echo.
echo 按任意键关闭...
pause >nul
exit /b 0

:no_python
echo [失败] 找不到 python。请先完成 1安装-组件.bat。
echo.
echo 按任意键关闭...
pause >nul
exit /b 1

:fail_dl
echo [失败] 下载中断，退出码=%DL%
echo 可重试本 bat，或：
echo   python tools\download_models.py --only paddleocr
echo   python tools\download_models.py --only whisper-small
echo   python tools\download_models.py --only cosyvoice --mirror modelscope
echo   python tools\download_models.py --all-light --mirror modelscope
echo.
echo 按任意键关闭...
pause >nul
exit /b 1
