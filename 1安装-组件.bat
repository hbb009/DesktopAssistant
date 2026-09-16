@echo off
title 桌面助手 v9.16 - 1安装组件
cd /d "%~dp0"

echo ========================================
echo   1/3  安装组件
echo   桌面助手 v9.16
echo ========================================
echo.
echo 本脚本合并了原先分步流程中的：
echo   旧1 安装-程序依赖
echo   旧4 安装-组件（ffmpeg/torch/paddle/OCR等）
echo   旧5 体检-组件
echo.
echo 会装这些【运行组件】（不含 model\ 大权重）：
echo   [A] requirements.txt          程序界面与基础库
echo   [B] ffmpeg                    录屏 / 音视频合并
echo   [C] requirements-models.txt   语音/OCR 相关 pip
echo       （含 paddleocr[doc-parser]，VL 必需）
echo   [D] torch / torchaudio        语音克隆基础
echo   [E] paddlepaddle(+GPU或CPU)   截图 OCR 底座
echo   [F] paddleocr[doc-parser]     PaddleOCR-VL 运行库
echo   [G] CosyVoice 源码 + 其 pip 依赖
echo.
echo 不会下载：PaddleOCR-VL / whisper / CosyVoice 大权重
echo （那些在 2安装-模型.bat）
echo.
echo 可选兜底（本步结束时如失败会提示）：
echo   paddle-cpu / diagnose_dll / fix_ocr
echo.
echo ----------------------------------------
echo 按任意键开始...
pause >nul
echo.

echo [1/7] 检查 Python ...
where python >nul 2>&1
if errorlevel 1 goto no_python
python --version
if errorlevel 1 goto no_python
echo [成功] Python 可用
echo.

echo [2/7] 升级 pip ...
python -m pip install -U pip
if errorlevel 1 goto fail_pip
echo [成功] pip 就绪
echo.

echo [3/7] 安装程序依赖 requirements.txt ...
echo （对应旧：1安装-程序依赖.bat）
python -m pip install -r requirements.txt
if errorlevel 1 goto fail_req
echo [成功] requirements.txt 完成
echo.

echo [4/7] 安装前体检（看缺什么）...
echo （对应旧：5体检-组件.bat）
python tools\setup_components.py --check
echo.
echo 上面表格仅供参考，接下来开始自动补齐运行组件。
echo 按任意键继续安装...
pause >nul
echo.

echo [5/7] 自动安装运行组件（对应旧：4安装-组件.bat）...
echo 内容：ffmpeg + deps + torch + paddle + paddleocr
echo       + CosyVoice 源码 + cosyvoice-deps
echo GPU 版 paddle 若导入失败，脚本会按默认改走 CPU 版。
echo 可能较久，请耐心等待，不要关闭窗口。
echo.
python tools\setup_components.py --yes --only ffmpeg --only deps --only torch --only paddle --only paddleocr --only cosyvoice-code --only cosyvoice-deps
set SC=%ERRORLEVEL%
echo.
if not "%SC%"=="0" (
  echo [警告] 自动安装退出码=%SC%，可能有项目未装全。
) else (
  echo [成功] 自动安装脚本结束
)
echo.

echo [6/7] 安装后体检 + OCR 真测 ...
echo （截图 OCR 会真测 PaddleOCRVL，不再只看包名；首次可能较慢）
echo 请看表格每一行是就绪还是缺口：录屏 / 语音 / 截图OCR / 语音克隆
python tools\setup_components.py --check
set CK=%ERRORLEVEL%
echo.
echo [判定说明]
echo   - 截图 OCR 就绪 = paddle + paddleocr[doc-parser] + 真测通过
echo     （大权重可在第2步再下；缺权重时表里会提示）
echo   - 语音克隆需 torch + Cosy源码 + cosy依赖；约9GB权重在第2步
echo   - GPU 版 paddle 反复失败时用：--only paddle-cpu
echo.
echo [7/7] 兜底与下一步
echo ----------------------------------------
if not "%SC%"=="0" goto need_repair
if not "%CK%"=="0" goto need_repair
echo [成功] 第 1 步流程走完。请再确认上方体检表。
echo.
echo 若「截图 OCR」仍不是就绪：
echo   1^) python tools\setup_components.py --only paddle-cpu --yes
echo   2^) python tools\setup_components.py --only paddleocr --yes
echo   3^) python tools\diagnose_dll.py ocr
echo   4^) tools\fix_ocr.bat
echo.
echo 下一步：双击  2安装-模型.bat  下载开源模型权重
echo 若刚装了 ffmpeg，之后请新开命令行再启动程序。
echo ----------------------------------------
echo.
choice /C YN /M "是否打开交互式组件菜单再修一次(旧4)"
if errorlevel 2 goto end_ok
if errorlevel 1 goto open_menu
goto end_ok

:need_repair
echo [警告] 自动安装或体检未完全通过。
echo 建议现在打开交互菜单（对应旧 4安装-组件），按提示补缺。
echo 也可稍后执行：
echo   python tools\setup_components.py --check
echo   python tools\setup_components.py
echo   python tools\setup_components.py --only paddle-cpu --yes
echo   python tools\diagnose_dll.py ocr
echo.
choice /C YN /M "现在打开交互式组件菜单"
if errorlevel 2 goto end_warn
if errorlevel 1 goto open_menu
goto end_warn

:open_menu
echo.
echo 进入交互菜单（可修录屏/语音/OCR/克隆/全部）...
python tools\setup_components.py
echo.
echo 菜单结束后再次体检：
python tools\setup_components.py --check
echo.
goto end_ok

:end_ok
echo.
echo 第 1 步结束。按任意键关闭...
pause >nul
exit /b 0

:end_warn
echo.
echo 第 1 步结束（仍有警告）。按任意键关闭...
pause >nul
exit /b 1

:no_python
echo.
echo [失败] 找不到 python，或不在 PATH。
echo 请安装 Python 3.10+，勾选 Add python.exe to PATH，
echo 重新打开窗口后再运行本 bat。
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
echo 请检查网络/镜像后重新运行。
echo.
echo 按任意键关闭...
pause >nul
exit /b 1
