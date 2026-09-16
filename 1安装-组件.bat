@echo off
title 桌面助手 v9.16 - 1安装组件
cd /d "%~dp0"

echo ========================================
echo   1/3  安装组件
echo   桌面助手 v9.16
echo ========================================
echo.
echo 本步会安装：
echo   - requirements.txt（程序依赖）
echo   - ffmpeg / torch / paddle
echo   - paddleocr[doc-parser]（截图 OCR 必需）
echo   - CosyVoice 源码与相关 pip 依赖
echo.
echo 不会下载 model\ 里的大模型权重。
echo 本步结束后，请再运行：2安装-模型.bat
echo.
echo ----------------------------------------
echo 按任意键开始安装...
pause >nul
echo.

echo [0/5] 检查 Python ...
where python >nul 2>&1
if errorlevel 1 goto no_python
python --version
if errorlevel 1 goto no_python
echo [成功] 已找到 Python
echo.

echo [1/5] 升级 pip ...
python -m pip install -U pip
if errorlevel 1 goto fail_pip
echo [成功] pip 就绪
echo.

echo [2/5] 安装 requirements.txt ...
python -m pip install -r requirements.txt
if errorlevel 1 goto fail_req
echo [成功] 程序依赖已安装
echo.

echo [3/5] 安装运行组件（可能较久，请耐心等待）...
echo 内容：ffmpeg、模型相关库、torch、paddle、
echo       paddleocr、CosyVoice 源码与依赖
echo.
python tools\setup_components.py --yes --only ffmpeg --only deps --only torch --only paddle --only paddleocr --only cosyvoice-code --only cosyvoice-deps
set SC=%ERRORLEVEL%
echo.
if not "%SC%"=="0" (
  echo [警告] 组件脚本退出码=%SC%，可能未全部装完。
  echo 可重新运行本 bat，或手动打开菜单：
  echo   python tools\setup_components.py
  echo.
) else (
  echo [成功] 组件脚本执行结束
  echo.
)

echo [4/5] 组件体检（请查看上方表格）...
python tools\setup_components.py --check
set CK=%ERRORLEVEL%
echo.
if not "%CK%"=="0" (
  echo [警告] 体检退出码=%CK%，请根据表格补缺。
) else (
  echo [成功] 体检已跑完，请确认表格里截图 OCR 等是否就绪。
)
echo.

echo [5/5] 第 1 步结束
echo ----------------------------------------
echo 下一步：双击运行  2安装-模型.bat
echo （把开源模型下载到 model\）
echo.
echo 注意：若本步刚装了 ffmpeg，之后请新开一个
echo 命令行窗口再启动程序，PATH 才会生效。
echo ----------------------------------------
echo.
echo 按任意键关闭本窗口...
pause >nul
exit /b 0

:no_python
echo.
echo [失败] 找不到 python，或不在 PATH 里。
echo 请先安装 Python 3.10+，勾选 Add python.exe to PATH，
echo 然后重新打开本窗口再运行。
echo.
echo 按任意键关闭...
pause >nul
exit /b 1

:fail_pip
echo.
echo [失败] pip 升级失败，请检查网络后重试。
echo.
echo 按任意键关闭...
pause >nul
exit /b 1

:fail_req
echo.
echo [失败] requirements.txt 安装失败。
echo 请检查网络/镜像后重新运行本 bat。
echo.
echo 按任意键关闭...
pause >nul
exit /b 1
