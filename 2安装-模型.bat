@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo  2/3  安装模型（写入 model\）
echo ========================================
echo.
echo model\ 下均为开源模型权重/源码，例如：
echo   PaddleOCR-VL-1.6、faster-whisper、Fun-CosyVoice3、CosyVoice 等。
echo 不进 Git；整包拷贝可跳过本步，缺哪个再下哪个。
echo.

if not exist "model" mkdir model

python tools\download_models.py %*
if errorlevel 1 goto fail

echo.
echo 完成。下一步：运行 3启动-程序.bat
pause
exit /b 0

:fail
echo.
echo [失败] 模型下载中断。可重试，或指定：
echo   python tools\download_models.py --only paddleocr
echo   python tools\download_models.py --all-light --mirror modelscope
pause
exit /b 1
