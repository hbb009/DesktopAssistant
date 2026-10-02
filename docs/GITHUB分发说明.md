# GitHub 分发说明

本项目已重新开源：GitHub 仓库只放**源码**，大文件不进仓库。

## 仓库里有什么

- 程序源码：`mainv917.py`、`ui_main.py`、`pages/`、`utils/`、`styles/`、`tools/`
- 资源：`assets/`（样式表、图标、功能说明图 WebP）
- 预设数据：`docs/内置预设_提示词分类.txt`、`docs/MiniMAX_H3_提示词分类.txt`（提示词编辑页启动时读取）
- 依赖清单：`requirements.txt`（程序）、`requirements-models.txt`（OCR / 语音 / 克隆等模型相关库）

## 不进仓库（见 .gitignore）

- 个人数据：`data/`（含 `user.txt` 设置与密钥）、`records/`、`VoiceEdit/`、`gameassist/`、`gamebrain/`
- 模型权重：`model/`（用 `python tools/download_models.py` 单独下载）
- 打包产物：`_internal/`、`runtime/`、`dist/`、`build/`、`*.exe`、`*.spec`
- 临时文件、缓存、日志：`temp/`、`__pycache__/`、`*.log`

## 安装与启动

1. `1安装-组件.bat`（或 `pip install -r requirements.txt`）
2. 需要 OCR / 语音转写 / 语音克隆时：`2安装-模型.bat`（或 `pip install -r requirements-models.txt` 后 `python tools/download_models.py`）
3. `3启动-程序.bat`（或 `python mainv917.py`）

系统组件：ffmpeg（`winget install Gyan.FFmpeg`）、Deno（`winget install DenoLand.Deno`，部分视频下载需要）。

## 密钥

仓库里不含任何 API Key。游戏大脑等功能的 OpenRouter Key 请在程序页面里填写，或设置环境变量 `OPENROUTER_API_KEY`（参考 `.env.example`）。

## 发布 exe

编译版通过 GitHub Releases 发布；打包时不要带上 `data/`、`records/`、`VoiceEdit/` 等个人数据目录。

提示词预设 txt 要随 exe 一起发：把 `docs/` 文件夹（含两份 `*_提示词分类.txt`）放在 exe 旁边。
查找顺序：exe 旁 `docs/` → 打包资源目录 `docs/`（PyInstaller `--add-data "docs;docs"`）→ 兼容旧版：exe 旁 / 资源目录根。
找不到内置预设时回退到代码里的默认预设；找不到 H3 文件时不显示 H3 按钮。
