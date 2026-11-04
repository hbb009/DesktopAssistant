# GitHub 分发说明

本项目已重新开源：GitHub 仓库只放**源码**，大文件不进仓库。

## 仓库里有什么

- 程序源码：`mainv918.py`、`ui_main.py`、`pages/`、`utils/`、`styles/`、`tools/`
- 资源：`assets/`（样式表、图标、功能说明图 WebP）
- 预设数据：`docs/提示词分类.txt`（单层【分类】列表；提示词编辑页启动时读取；>15 条时 UI 用蓝色 H3 溢出）
- 依赖清单：`requirements.txt`（程序）、`requirements-models.txt`（OCR / 语音 / 克隆等模型相关库）

## 不进仓库（见 .gitignore）

- 个人数据：`data/`（含 `user.txt` 设置与密钥）、`records/`、`VoiceEdit/`、`gameassist/`、`gamebrain/`
- 模型权重：`model/`（用 `python tools/download_models.py` 单独下载）
- 打包产物：`_internal/`、`runtime/`、`dist/`、`build/`、`*.exe`、`*.spec`
- 临时文件、缓存、日志：`temp/`、`__pycache__/`、`*.log`

## 安装与启动

从 GitHub 克隆后按电脑配置选一个。需要 Python 3.10 或 3.11。

- `A安装.bat`：低配。只装程序组件（`requirements.txt`、ffmpeg、Deno），不装 torch / paddle / 语音克隆，也不下载 `model/` 权重。下载、速存、粘贴、文本、看图、阅读这些基本功能可以用。
- `B安装.bat`：高配。在低配之上安装模型运行库，并下载截图 OCR、三档语音转写、中英翻译、语音克隆权重。AuK、Qwen2.5-Omni-3B、BiRefNet、Upscayl 仍需手动放进 `model/`。

两个安装脚本结束时会打开一次程序。以后每天启动用 `3启动-程序.bat`（或 `python mainv918.py`）。

等价命令：`python tools/install_ab.py basic` 或 `python tools/install_ab.py full`。

系统组件：ffmpeg（`winget install Gyan.FFmpeg`）、Deno（`winget install DenoLand.Deno`，部分视频下载需要）。高配克隆 CosyVoice 源码时还需要 Git。

## 密钥

仓库里不含任何 API Key。游戏大脑等功能的 OpenRouter Key 请在程序页面里填写，或设置环境变量 `OPENROUTER_API_KEY`（参考 `.env.example`）。

## 发布 exe

编译版通过 GitHub Releases 发布；打包时不要带上 `data/`、`records/`、`VoiceEdit/` 等个人数据目录。

提示词预设 txt 要随 exe 一起发：把 `docs/` 文件夹（含 `提示词分类.txt`）放在 exe 旁边。
查找顺序：exe 旁 `docs/` → 打包资源目录 `docs/`（PyInstaller `--add-data "docs;docs"`）→ 兼容旧版：exe 旁 / 资源目录根。
找不到预设文件时回退到代码里的默认预设；某栏 ≤15 条时不显示 H3 按钮（仍兼容旧双文件名 / `## 内置|H3` 标记作回退合并）。
