<img width="962" height="918" alt="4ee36bfa-db90-4126-ab94-69d449a373d5" src="https://github.com/user-attachments/assets/d5a9a299-fef2-4089-a934-477ad6f9bb4a" />

# 桌面助手 v9.16 · 给 AI 人的口袋瑞士军刀 🔧🧠

> 让每一个操作，都能为您省下宝贵的时间

---

## 🆕 v9.16 主要调整

1. **新增「语音克隆」页**（助手区）：基于本地 CosyVoice3-0.5B，左侧上「声音卡」（拖入/录制参考原声，30 秒以内）下「文案」，右侧「生成记录」列表；同 OCR / 语音转写一样跑在独立子进程（`--cosyvoice-job`），避免与主进程 Qt 抢占 PyTorch/onnxruntime 的 DLL；模型权重放 `model/Fun-CosyVoice3-0.5B`，官方推理源码另需克隆 `CosyVoice` 仓库到 `model/CosyVoice`；素材与生成记录存 `data/voice_clone/`。
2. **新增「游戏助手」页**（助手区）：攻略知识库 + 截图 + 后台 Ollama 检索问答，回答可能给出多个候选答案，一键切换；知识库索引单独放项目根 `game_assist/` 目录（不进 `data/`，不随「数据管理」的导出/重置清空）；需要本机已安装并运行 Ollama。
3. **截图 OCR 引擎换成 PaddleOCR-VL-1.6**（原 RapidOCR 已下线）：权重放 `model/PaddleOCR-VL-1.6`；新增 `--ocr-server` **常驻子进程模式**——识别管线只在首次加载一次，后续截图识别直接复用同一进程，不再每次重新起进程加载模型；原有单次子进程模式 `--ocr-job` 仍保留用于探测/兜底；`tools/fix_ocr.bat` 同步改为安装 `paddlepaddle` / `paddleocr`。
4. **运行数据目录统一收进 `data/`**：原来分散的 `records/`、`gallery/`、`cards/`、`prompts/` 四个目录合并为单一 `data/`（含 `app.log`、`crash_*.log`、`user.txt`、`pre_download.json`、`gallery_eh.txt`、`gallery_hitomi.txt`、`cards/`、`prompts/`、`voice_clone/` 等），启动时自动一次性迁移旧目录内容，无需手动搬家。
5. **新增 `model/` 目录**统一存放本地大模型权重（faster-whisper、PaddleOCR-VL-1.6、CosyVoice3-0.5B 等），体积较大，独立于 `data/`，不计入「数据管理」的导出/重置范围。
6. **入口更新为 `mainv916.py` / 便携包 `mainv916.exe`**：新增 `--whisper-job`（语音转写子进程，沿用自 v9.15）、`--ocr-job` / `--ocr-server`（截图 OCR 单次/常驻子进程）、`--cosyvoice-job`（语音克隆子进程）共四类「必须在 import PyQt5 之前退出」的早退分支，进一步隔离原生库与主进程 Qt 的 DLL 冲突；启动前设 `QT_SCALE_FACTOR` 的逻辑延续自 v9.13 的修复；新增硬崩溃兜底（`faulthandler` 注册，原生 access violation 时把各线程 Python 栈写入 `data/crash_*.log`，启动时只保留 2 小时内 + 最近两次的记录）。
7. **便携发布补齐运行时与工具链**（对应目录 `mainv916/`）：内置 `runtime\python`（Windows embeddable Python 3.11，供 OCR / Whisper / CosyVoice 子进程）；`_internal\ffmpeg\bin\` 与 `_internal\deno\` 打包 ffmpeg / ffprobe / Deno，整夹复制到另一台电脑即可用，不必再装 winget/scoop；推荐用根目录 `启动.bat` 启动（自动把工具目录加入 PATH）。另附 `自检.bat`、`发布说明.md`。

---

## 🗂️ 功能模块

侧边栏分三区：**置顶常用** → **助手 · 主手脚** → **工具 · 小偏门**（靠下），底部为「关于」；顶栏另有「电子钟」图标入口（不占侧栏位置）。
> 侧栏按钮可**按住拖拽排序**（可跨分区自由摆放），分区标题可自定义改名，顺序与命名自动保存到 `data/user.txt`。

### 置顶常用

- **🖥️ 系统总览**：显示环境信息（设备名 / 处理器 / 机带 RAM / 系统类型与版本 / Python 版本 / 磁盘空间）与资源监控（GPU 使用率、显存、内存、CPU、GPU 温度、GPU 功耗、硬盘使用，实时刷新百分比+进度条）；下方版本信息区读取项目根目录 `README.md` / `README_v9_16.md` 展示更新日志。设置区（界面缩放、保存与 Cookie、数据管理）与「功能与说明」教程轮播卡均在本页；「预下载」卡片（自动处理开关 + 「预处理文件」按钮，可整表查看/编辑六平台 + 无法处理队列）。
- **📥 速存图文**：配套浏览器插件，图片悬停按快捷键静默保存、文本全局快捷键提取，自动分类建目录；开关一键启停「速存全功能」。
- **🎬 视频下载**：抖音 / B站 / YouTube 三个 Tab 子页，支持解析下载与统一跨平台批量下载；剪贴板命中白名单后进入预下载队列自动处理，页签角标显示排队条数；下载遇到同名文件会弹窗确认（中止/改名/覆盖），单条内容 180 秒无进度自动判定失败并跳过。YouTube / B站合并成片依赖 ffmpeg；YouTube JS 挑战依赖 Deno（便携包已内置，见下方「便携包」）。
- **🖼️ 图集下载**：粘贴漫画/图站网址批量抓图，支持 e-hentai/ExHentai、hitomi.la、Pixiv；同样接入预下载队列（页签角标显示条数）与同名文件确认、下载防卡死保护。
- **✂️ 截图工具**：自定义热键框选截图，自动存图/转格式/复制剪贴板，内置本地 OCR 识字；**v9.16 起识别引擎换为 PaddleOCR-VL-1.6**，并支持常驻子进程模式（首次加载后续复用，识别更快）；开关为「截图监听」一键开/关热键。
- **⏺ 区域录屏**：热键定位/调整选区后开录，热键停止保存；按钮旁圆点在录制中显示红点提示；依赖 ffmpeg（便携包已内置）。

### 助手 · 主手脚

- **📝 粘贴助手**：主题 + 内容 + 七色标签存为卡片，流式布局可拖拽排序，点击复制并载入编辑区；预下载记录也可一键转出为本页记录卡。
- **🖌️ 图片处理**：选图后缩小 / 切割 / 转格式，输出预览区可滚动查看历史处理结果，单击打开输出目录。
- **💰 积分计算**：按订阅金额、汇率、单次消耗积分，折算单图/单秒视频的真实成本，历史可保存。
- **🎙 语音编辑**：本地语音转文字（子进程中加载模型，避免与 Qt 冲突崩溃），转写结果进主编辑区改字、分段、排版后一键复制；繁体输出自动转简体（`zhconv`）。
- **🗣️ 语音克隆**（v9.16 新增）：拖入/录制参考原声（30 秒以内）+ 填写文案，本地 CosyVoice3-0.5B 合成克隆语音，右侧生成记录可回放/管理；同样在独立子进程中运行。
- **🎮 游戏助手**（v9.16 新增）：导入攻略文本/文件建知识库，截图后台送本机 Ollama 检索，给出可切换的多个候选答案；支持视觉模型直接读图作答。
- **📋 提示词编辑器**：文生视频提示词左右对照编辑，字段化管理（基础参数/镜头/人物/体型/服装/环境/动作/表演/台词等），历史记录自动保存。

### 工具 · 小偏门

- **比例计算**：常用画幅像素换算，附金额大小写转换。
- **时区汇率**：多城市模拟时钟（可增删/可模拟时间）+ 汇率转换器。
- **目录映射**：图形化软链接创建，附磁盘空间 Treemap 可视化。

### 顶栏入口（不占侧栏）

- **⏰ 电子钟**：点击顶栏时钟图标进入，七段数码时分 + 月历 + 天气卡（天气界面先行，实况接口待接入）。

### 关于

自动下载白名单管理（e-hentai / hitomi.la / Pixiv 等分组），及底部 GitHub/主题切换等链接。软件信息、界面缩放、保存与 Cookie、数据管理见「系统总览」页。

---

## 🛠️ 安装与运行

### A. 源码运行

> 建议 **Python 3.10+ / Windows 10+**（部分能力仅限 Windows）

三步安装（推荐；合并了原先 1程序依赖 / 2模型依赖 / 3启动 / 4组件 / 5体检）：



`at
1安装-组件.bat
2安装-模型.bat
3启动-程序.bat
`

1. **安装组件**：
equirements.txt + ffmpeg / torch / paddle / paddleocr[doc-parser] / CosyVoice 源码依赖等（按本机 CUDA 选轮子）。
2. **安装模型**：把开源模型下到 model\（PaddleOCR-VL-1.6、faster-whisper、Fun-CosyVoice3 等）。model/ 不进 Git；整包已带权重可跳过。
3. **启动程序**：运行桌面助手。也可直接 python mainv916.py。

装完组件后请**完全退出再开**；装过 ffmpeg 请**新开命令行窗口**再启动。

排障：

`at
python tools\setup_components.py --check
python tools\diagnose_dll.py ocr
python -m pip install -U "paddleocr[doc-parser]>=3.6.0"
`

**关于管理员权限**：默认普通用户启动，需要时可显式提权 python mainv916.py --as-admin；目录映射页的 mklink 也可单独勾选管理员执行。


### B. 便携包（`mainv916/` 整夹分发）

对照当前发布目录：

| 路径 | 说明 |
|------|------|
| `mainv916.exe` | 主程序（也可双击直接开） |
| `启动.bat` | **推荐**：把 `_internal\ffmpeg\bin`、`_internal\deno`、`_internal` 预置进 PATH 后再启动 |
| `自检.bat` | 环境自检 |
| `发布说明.md` | 便携运行时 / 工具补充说明 |
| `_internal\` | PyInstaller 依赖 + 便携工具（见下） |
| `runtime\python\` | 内置 embeddable **Python 3.11.9**，OCR / Whisper / CosyVoice 子进程优先用它，不依赖本机其它 Python |
| `model\` | 本地大模型权重（体积大，可按需拷贝） |
| `game_assist\` | 游戏助手知识库索引 |
| `data\` | 运行数据（日志、偏好、预下载、语音克隆记录等） |
| `tools\` | 辅助脚本（如 OCR 修复） |
| `README_v9_15.md` / `README_v9_16.md` | 版本说明 |

**便携工具（YouTube / 录屏，无需 winget/scoop）：**

| 工具 | 路径 | 用途 |
|------|------|------|
| ffmpeg / ffprobe | `_internal\ffmpeg\bin\` | YouTube/B站音视频合并、区域录屏 |
| Deno | `_internal\deno\deno.exe` | YouTube 签名挑战（JS 运行时） |

> 换电脑：整夹复制即可。若直接双击 exe 仍提示缺 ffmpeg / Deno，改用 `启动.bat`。建议目标机安装 [VC++ Redistributable x64](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)。

**可选环境（源码与便携通用）**
- **NVIDIA**：GPU 监控需 `nvidia-smi` 在 PATH；OCR / 语音克隆在有 GPU 时更快，无 GPU 也可 CPU 跑（更吃内存）。
- **yt-dlp + yt-dlp-ejs + ffmpeg + Deno/Node.js**：视频下载 B站/YouTube 子页 + 区域录屏需要；便携包已带 ffmpeg 与 Deno；`yt-dlp-ejs` 用于处理 JS 签名反爬校验，建议随 `yt-dlp` 一同升级。
- **faster-whisper + sounddevice**：语音编辑转写需要；转写在独立子进程中加载，结果自动繁转简（`zhconv`）；权重放 `model/`。
- **paddlepaddle + paddleocr[doc-parser]**（**v9.16 起替代 rapidocr-onnxruntime**）：截图工具 OCR 识字（可选），权重放 `model/PaddleOCR-VL-1.6`（约 1.9GB）；支持常驻模式；异常可用 `tools/fix_ocr.bat` 一键修复。
- **CosyVoice3（v9.16 新增，可选，体积大）**：语音克隆需要，权重放 `model/Fun-CosyVoice3-0.5B`，官方推理源码另需 `git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git` 到 `model/CosyVoice`；PyTorch 请按本机 CUDA 情况安装。
- **Ollama（v9.16 新增，可选）**：游戏助手的检索问答需要本机已安装并运行 Ollama，支持文本模型与视觉模型。
- **浏览器扩展（MV41）**：配合速存图文使用。
- **Get cookies.txt LOCALLY（浏览器扩展）**：导出 YouTube / e-hentai / hitomi.la / Pixiv 等站点 Cookie（Netscape 格式）。
> 磁盘 Treemap、版本信息、「功能与说明」轮播、电子钟均已改用 Qt 原生渲染（QPainter / QTextBrowser / QLabel），不再依赖 PyQtWebEngine。

### 🧩 浏览器扩展（MV41）安装步骤

1. Chrome 地址栏输入 `chrome://extensions/`
2. 打开右上角「开发者模式」
3. 「加载已解压的扩展程序」→ 选择项目 `MV41` 文件夹

---

## 📁 代码结构

```
桌面助手/
├─ mainv916.py                  # 入口（默认普通用户；--as-admin 显式提权；启动前设 QT_SCALE_FACTOR）
│                                 #   --whisper-job / --ocr-job / --ocr-server / --cosyvoice-job：
│                                 #   语音转写 / 截图 OCR（单次、常驻） / 语音克隆 各自独立子进程
│                                 #   硬崩溃兜底：faulthandler → data/crash_*.log
├─ ui_main.py                   # 主窗口 + 侧栏三分区 + 拖拽排序 + 分区改名 + 顶栏（含电子钟入口）
├─ requirements.txt / requirements v9.16.txt
├─ pages/
│  ├─ page_overview.py          # 系统总览（含设置区 + 预下载卡片 + 功能与说明轮播）
│  ├─ settings_section.py       # 设置区可复用组件（缩放/保存与Cookie/数据管理/预下载）
│  ├─ pre_download_panel.py     # 预下载记录浏览/编辑弹窗
│  ├─ page_fast_save.py         # 速存图文
│  ├─ page_video.py             # 视频下载（Tab 容器 + 统一批量下载 + 预下载角标）
│  ├─ page_douyin.py / page_bilibili.py / page_youtube.py   # 三平台子页
│  ├─ page_gallery.py           # 图集下载（e-hentai/ExHentai + hitomi.la + Pixiv）
│  ├─ page_screenshot.py        # 截图工具（含 OCR 识字，PaddleOCR-VL-1.6，子进程运行）
│  ├─ page_region_record.py     # 区域录屏
│  ├─ page_paste.py             # 粘贴助手
│  ├─ page_image_proc.py        # 图片处理（缩小/切割/转格式）
│  ├─ page_voice_input.py       # 语音编辑（本地转写，子进程运行，繁转简）
│  ├─ page_voice_clone.py       # 语音克隆（v9.16 新增：CosyVoice3 本地克隆，子进程运行）
│  ├─ page_game_assist.py       # 游戏助手（v9.16 新增：知识库+截图+Ollama 检索问答）
│  ├─ page_prompt_editor.py     # 提示词编辑器（文生视频提示词字段化编辑）
│  ├─ page_female_char.py       # 角色提示词母模板（代码内已实现，当前未挂入侧栏导航）
│  ├─ page_points_calc.py       # 积分计算
│  ├─ page_ratio_calc.py / page_dir_link.py / page_timezone_fx.py  # 工具·小偏门
│  ├─ page_clock.py             # 电子钟（七段数码时钟+月历+天气卡，顶栏入口）
│  ├─ page_about.py             # 关于（自动下载白名单管理，含 Pixiv）
│  └─ disk_treemap_widget.py    # 磁盘空间 Treemap 可视化
├─ tools/
│  ├─ hitomi_zip_debug.py       # hitomi.la 下载逻辑核实/调试脚本
│  └─ fix_ocr.bat               # OCR 依赖一键修复脚本（v9.16 更新为 PaddleOCR-VL）
├─ utils/                       # 通用工具（文件/日志/布局/语音转写/OCR/语音克隆/游戏助手等）
│  ├─ download_confirm.py       # 下载前同名文件三选一确认（中止/改名/覆盖）
│  ├─ download_watchdog.py      # 下载防卡死看门狗（180s 无进度即判失败）
│  ├─ pre_download.py           # 预下载队列：六平台 + 无法处理，持久化到 data/pre_download.json
│  ├─ ocr_util.py / ocr_job.py  # 本地 OCR 识字（v9.16 换 PaddleOCR-VL-1.6，含常驻服务模式）
│  ├─ voice_input.py / whisper_job.py     # 语音转写（本地录音 + 子进程执行）
│  ├─ cosyvoice_clone.py / cosyvoice_job.py  # 语音克隆（v9.16 新增）
│  ├─ game_assist.py            # 游戏助手（v9.16 新增：知识库索引/检索 + Ollama 对话，不依赖 Qt）
│  ├─ region_recorder.py        # 区域录屏核心逻辑
│  ├─ deskassist_runtime.py     # 便携 runtime Python 探测与子进程环境
│  ├─ qthread_util.py           # 统一安全停止 QThread（关窗清理）
│  ├─ app_paths.py              # 统一路径解析（data/model/game_assist/voice_clone 等）
│  ├─ gallery_records.py        # 图集下载记录
│  └─ cursor_toast.py           # 全局鼠标旁轻提示气泡
├─ styles/                      # 主题样式（app.qss / app_light.qss / style_all.py）
├─ assets/guides/               # 「功能与说明」轮播教程图
├─ model/                       # （v9.16 新增）本地大模型权重
├─ game_assist/                 # （v9.16 新增）游戏助手知识库索引，独立于 data/
├─ data/                        # （v9.16 起统一）运行数据：user.txt / app.log / crash_*.log /
│  │                             #   pre_download.json / gallery_*.txt /
│  ├─ cards/                    #   粘贴助手记录卡
│  ├─ prompts/                  #   提示词编辑器数据
│  ├─ voice_clone/              #   语音克隆参考原声、合成结果、生成记录
│  └─ guides/                   #   用户自定义教程图
├─ MV41/                        # 配套浏览器扩展
│
│  # —— 便携包额外目录（发布用 mainv916/）——
├─ mainv916.exe / 启动.bat / 自检.bat / 发布说明.md
├─ runtime\python\              # 内置 Python 3.11.9（子进程）
└─ _internal\                   # 依赖 + ffmpeg\bin + deno\
```

> 主脑配置、导演台、Ollama 工具、反推生图等 AI 相关页面（`page_brain_config.py`、`page_api_config.py`、`page_model_service.py`、`page_director.py`、`page_ollama_tools.py`、`page_literary_writing.py`、`page_prompt_gen.py`、`utils/llm_client.py`、`utils/mini_brain_client.py`、`utils/ollama_client.py`）已从主程序拆出，将并入独立的「算力版」程序。
> 「数字人」（SadTalker 数字人对话）功能已开发但暂存至 `BACK/digital_human`，主程序当前不加载；待后续视情况再接回主程序。

---

## ❗ 常见问题

- **`ModuleNotFoundError: No module named 'utils'`** → 从项目根目录运行 `python mainv916.py`。
- **资源监控无 GPU 数据** → 安装 `psutil`；无 NVIDIA 或 `nvidia-smi` 不在 PATH 时显示 0。
- **语音编辑转写无反应** → 需安装 `faster-whisper` + `sounddevice`，首次使用可能联网下载模型；转写在独立子进程中加载，若子进程启动失败请查看 `data/app.log`；便携包优先用 `runtime\python`。
- **B站 / YouTube 无法解析 / 提示缺 ffmpeg 或 Deno** → 源码环境需安装 `yt-dlp`（建议同时装 `yt-dlp-ejs`）、`ffmpeg`、Deno/Node；**便携包请用 `启动.bat` 打开**，并确认 `_internal\ffmpeg\bin\` 与 `_internal\deno\deno.exe` 存在。
- **区域录屏提示找不到 ffmpeg / 无法 winget 安装** → 便携包已内置 ffmpeg，勿依赖系统 winget/scoop；整夹复制后用 `启动.bat`。
- **图集下载访问 ExHentai / hitomi.la / Pixiv 失败** → 需导入有效 Netscape 格式 Cookie；hitomi.la 可用 `tools/hitomi_zip_debug.py` 单独核实；Pixiv 需先在站内登录后导出 Cookie。
- **下载一直卡在某一条不动** → 已有防卡死看门狗，180 秒无新数据会自动判定失败并跳过；若仍长时间无响应，请检查网络或站点是否失效。
- **下载提示「已存在同名文件」** → 弹窗默认 10 秒后自动中止，也可手动选择改名或覆盖；此确认对视频下载与图集下载均生效。
- **预下载队列不自动继续 / 一直停着** → 确认系统总览「预下载」卡片开关是否已打开；本机若未加载对应平台 Cookie，启动时会警告并自动关闭该开关；也可点「预处理文件」手动查看/编辑队列。
- **调整界面缩放后重启没生效** → v9.13 曾有该 bug，已在入口文件中修复；仍异常可检查 `data/user.txt` 里 `ui.scale` 是否写入正确。
- **速存图片插件状态灯黄/红** → 黄=等待连接或心跳超时；红=端口占用或 Chrome 未运行，确认以管理员权限运行或放行安全软件白名单。
- **截图 OCR 无法识字** → v9.16 起需安装 `paddlepaddle`（或 `paddlepaddle-gpu`）与 `paddleocr[doc-parser]`，并把 `PaddleOCR-VL-1.6` 权重放到 `model/`；可运行 `tools/fix_ocr.bat` 一键修复，或到「截图工具」内点「检测/修复」。
- **语音克隆没反应 / 提示缺权重** → 需把 `Fun-CosyVoice3-0.5B` 权重放到 `model/`，并额外克隆官方推理源码到 `model/CosyVoice`；参考原声超过 30 秒需先裁剪。
- **游戏助手连不上模型 / 检索无结果** → 需本机安装并运行 Ollama（`ollama serve` 或桌面客户端）；知识库为空时先导入攻略文本/文件建索引。
- **换电脑后缺 DLL / 子进程起不来** → 安装 VC++ x64 运行库；保持 `_internal` 与 `runtime` 与 exe 同目录整夹复制，不要只拷单个 exe。
- **找不到「图片处理」「语音克隆」「游戏助手」「提示词编辑器」「电子钟」** → 前四者在侧栏「助手 · 主手脚」区；电子钟不在侧栏，点击顶栏时钟图标进入。

---

## ✅ 开发规范

- **样式与逻辑分离**：视觉样式写入 `assets/*.qss` 与 `styles/style_all.py`；布局与业务逻辑写在 Python。
- **优雅降级**：`psutil` / `nvidia-smi` / `yt-dlp` / `faster-whisper` / `paddleocr` / CosyVoice / Ollama 不可用时提示但不崩溃。
- **Windows 依赖隔离**：`pywin32`、WinAPI 相关代码均有 `try/except` 保护。
- **子进程隔离**：OCR、语音转写、语音克隆等易与主进程 Qt/OpenMP 抢占 DLL 的能力，统一放到独立子进程（`--ocr-job` / `--ocr-server` / `--whisper-job` / `--cosyvoice-job`）执行；便携包子进程优先走 `runtime\python`。
- **后台线程与关窗清理**：涉及定时器/`QThread` 的页面需在 `ui_main.py` 的 `closeEvent` 统一停止，见 `utils/qthread_util.py`。
- **持久化原子写入**：用户配置「写 `.tmp` + `os.replace`」，见 `utils/user_prefs.py`。
- **数据与模型分层**：轻量运行数据统一进 `data/`（随导出/重置管理）；大体积模型权重进 `model/`，游戏助手知识库单独放根目录 `game_assist/`，两者均不计入数据导出/重置范围。
- **便携优先**：发布目录应自带 runtime + ffmpeg + Deno，避免目标机再装系统级工具链。

---

## 📜 版本历史

| 版本 | 主要内容 |
| --- | --- |
| **v9.16 (当前)** | 新增「语音克隆」（本地 CosyVoice3-0.5B，独立子进程）、「游戏助手」（知识库+截图+Ollama 检索问答，多候选答案）；截图 OCR 由 RapidOCR 换为 PaddleOCR-VL-1.6，新增常驻子进程模式；运行数据统一进 `data/`，新增 `model/`；入口 `mainv916.py` / 便携 `mainv916.exe`，崩溃日志兜底；便携包内置 `runtime` Python 与 `_internal` 下的 ffmpeg、Deno，可用 `启动.bat` 整夹迁移。 |
| **v9.15** | 图集下载新增 Pixiv 支持；新增「图片处理」「提示词编辑器」两个助手区页面；新增顶栏「电子钟」入口；「预下载」体系重构（剪贴板白名单→六平台+无法处理队列，系统总览新增预下载卡片，下载页签显示排队角标，记录可转出为粘贴助手卡片）；截图 OCR 与语音转写改为独立子进程运行，减少 DLL 冲突；新增 `yt-dlp-ejs` 依赖；入口改为 `mainv915.py`。 |
| **v9.14** | 设置区（软件信息/界面缩放/保存与Cookie/数据管理）从「关于」页迁移到「系统总览」页，并新增「功能与说明」图文轮播卡；图集下载新增 hitomi.la 支持；下载新增同名文件确认与防卡死看门狗；新增全局鼠标提示气泡；侧栏默认分区调整（截图/录屏入置顶）；修复界面缩放重启失效的 bug；语音编辑转写结果自动繁转简。 |
| **v9.13** | Ollama/API 相关的主脑配置、导演台、Ollama 工具、反推生图等 AI 页面从主程序拆分，后续独立为「算力版」程序；侧栏主按钮支持跨分区拖拽排序 + 分区改名；视频下载新增 B站子页与跨平台统一批量下载；新增「语音编辑」（本地转写）。 |
| **v9.12** | 版本号升级至 v9.12，入口改为 `mainv912.py`。 |
| **v9.11** | 主大脑配置改为顶栏「本地/API」开关 + ⚙ 弹窗；模型条与粘贴助手 UI 打磨；抖音解析软过滤兜底；关于页可点 GitHub、复制联系方式。 |
| **v9.10** | 抖音下载升级为多平台视频下载（+YouTube）；新增图集下载；新增模型选择全局主大脑；侧栏三分区重排；默认普通用户启动。 |
| **v9.9** | 新增粘贴助手、导演台、文学写作；反推提示词与批量打标合并为 Ollama 工具；抖音新增一键粘贴解析；速存图文改 WebSocket 心跳。 |
| **v9.8** | 新增抖音无水印解析、积分计算、图形化目录映射与磁盘分析；速存图文新增插件直连。 |
| **v9.5** | 样式体系统一；速存图文后台常驻；批量打标真实接入 Ollama。 |
| **v9.0** | 主题与字体统一；系统总览资源监控；反推提示词、比例计算、批量打标上线。 |
| **v8.1** | 速存图文自动/手动模式；截图工具热键框选；Ollama 助理流式聊天。 |
