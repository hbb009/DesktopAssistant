# 桌面助手 v9.17 · 给 AI 人的口袋瑞士军刀 🔧🧠

> 让每一个操作，都能为您省下宝贵的时间

**v9.17 起重新开源**（MIT 许可）：仓库放完整源码，模型权重与个人数据不进仓库；编译好的 exe 通过 GitHub Releases 发布。

---

## 🆕 v9.17 主要调整

1. **新增「音频处理」页**（助手区，原「语音克隆」页并入）：上方共用「声音卡」与播放行，下分两个页签——
   - **AUK**：对选中的声音卡做 **降噪 / 改词 / 配音 / 改情绪（开心·悲伤·生气·温柔·平静）/ 转耳语**，带「省显存」开关；推理跑在独立子进程（`--auk-job --serve`），权重放 `model/AuK`、`model/Qwen2.5-Omni-3B`，运行库放 `model/AuK-src`。
   - **CosVoice3**：即原语音克隆面板（本地 Fun-CosyVoice3-0.5B，参考原声 30 秒以内），仍走 `--cosyvoice-job` 子进程。
   - 两个页签的操作写进同一条记录时间线。AuK 推理放在自己的子进程里，但与主程序共用同一个 Python 环境；加它时不升级主环境的公共包（曾因升级 transformers 让 CosyVoice 生成乱声，见「手动安装的可选组件」）。
2. **新增「音频下载」页**（置顶区）：版式对齐抖音页，目前支持 **汽水音乐**，接入预下载队列与角标。
3. **视频下载新增「其它视频」页签**：**即梦 / LibTV / XVideos / Pornhub** 共用一套「解析 → 画质卡 → 下载」流程；XVideos 默认勾选最高画质（HLS 优先），HLS 分片下载后由 ffmpeg 无损封装为 mp4。
4. **游戏三件套**（助手区）：
   - **游戏助手** 改为 **OpenAI 兼容大模型 API**（预设 DeepSeek / OpenAI / Kimi / 智谱 GLM / 通义千问 / 豆包 / 腾讯混元 / Ollama 本机 / LM Studio 本机），流程：知识库 + 截图 → 本地 OCR 识字（可编辑）→ 检索知识库 → API 作答，多答案可切换；知识库目录由 `game_assist/` 改名为 `gameassist/`（启动时自动迁移）。
   - **新增游戏大脑**：文本区内容直接送大模型 API 作答（不检索知识库），默认 **OpenRouter + Jev 决策模型**（`typesafe/jev-1.13`，走 OpenRouter Decisions API，见 `docs/jev_api.md`），也可切换其它兼容服务；结果写入「交互记录」。
   - **新增游戏会议**：局域网共享文档（TCP JSON-lines 同步 + UDP 房间发现），成员编辑权勾选、历史数据；全程后台静默，不抢游戏窗口焦点。
5. **全局 F7「静默复制」+ 侧栏复制归属图标**：「粘贴助手 / 游戏大脑 / 游戏会议」三个侧栏按钮旁各有一个复制归属槽（亮绿 = 当前归属），按 F7 会在不抢焦点的情况下复制前台选中文字并推送到归属页；静默复制总开关关掉即注销 F7，让给游戏或其它程序。
6. **提示词编辑器预设改为外置 txt**：`docs/内置预设_提示词分类.txt`（79 条）与 `docs/MiniMAX_H3_提示词分类.txt`（1223 条），启动时读取一次；每栏「自定义 + 内置 + H3」按内容去重合并，**前 15 条直接显示，其余折进「H3 ▸」**；点过（加入或取消）的条目自动提到本栏最前（**最近使用排序**，每栏最多记 200 条）。找不到内置 txt 时回退到代码内置预设，找不到 H3 txt 时不显示 H3 按钮。
7. **「语音编辑」拆分**：录音转写页更名 **「语音录入」**（只留录音 + 转写结果），排版整理独立成 **「文本编辑」** 页（工具区），转写记录卡可「跳转文本页」。
8. **图片处理新增「放大」与「抠图」**：放大调用 Upscayl / Real-ESRGAN（6 个 4× 模型，输出 2–6 倍），抠图用 BiRefNet-general ONNX（独立子进程 `--matting-job`）。
9. **运行数据拆成 `data/` + `records/`**：`data/` 放程序运行数据（`user.txt`、`app.log`、`crash_*.log`），`records/` 放用户创造的数据（记录卡、图集已下记录、粘贴/积分记录、提示词、预下载队列等）；首次运行自动创建，旧布局启动时自动迁移。
10. **系统总览改到侧栏底栏**：底栏固定「系统总览 + 「预」开关 + 主题切换（☀/🌙）」；原「关于」页取消，组件安装与预下载白名单并入系统总览；设置区新增「公共保存」（各功能统一的保存根目录）。
11. **电子钟空闲跳转**：按侧栏分区空闲一段时间（置顶 5 分钟 / 助手 10 分钟 / 工具 3 分钟 / 系统总览 3 分钟）自动切到电子钟。
12. **其它**：功能说明图改为 WebP（体积约为原 PNG 的 1/13）；API Key 不再写在代码里（见下方「🔑 配置」）；入口改为 `mainv917.py`。

---

## 🗂️ 功能模块

侧边栏分三区：**置顶常用** → **助手 · 主手脚** → **工具 · 小偏门**，底栏固定「系统总览」+「预」开关 + 主题切换；顶栏另有「电子钟」图标入口（不占侧栏位置）。
> 侧栏按钮可**按住拖拽排序**（可跨分区自由摆放），分区标题可自定义改名，顺序与命名自动保存到 `data/user.txt`。

### 置顶常用

- **🎬 视频下载**：抖音 / B站 / YouTube / 其它视频（即梦、LibTV、XVideos、Pornhub）四个 Tab 子页，支持解析下载与跨平台批量下载；剪贴板命中白名单后进入预下载队列自动处理，页签角标显示排队条数；下载遇到同名文件会弹窗确认（中止/改名/覆盖，10 秒后默认中止），单条内容 180 秒无进度自动判定失败并跳过。YouTube / B站合并成片依赖 ffmpeg；YouTube JS 挑战依赖 Deno。
- **🎵 音频下载**（v9.17 新增）：目前支持汽水音乐，同样接入预下载队列与角标。
- **🖼️ 图集下载**：粘贴漫画/图站网址批量抓图，支持 e-hentai/ExHentai、hitomi.la、Pixiv；接入预下载队列、同名文件确认、下载防卡死保护。页底「已下记录」防重复下载（e-hentai 记文件夹名、hitomi 记 zip 名，Pixiv 不记），记录存 `records/gallery_eh.txt` / `gallery_hitomi.txt`，并会扫描保存目录里已有的作品自动补全。
- **📥 速存图文**：配套浏览器扩展（经本机 WebSocket `127.0.0.1:19876` 连接），图片悬停按快捷键静默保存、文本全局快捷键提取；另有速建文件夹、序列文件检查、「去除同名」（文件名与大小都相同才把较旧的版本移入回收站）。
- **✂️ 截图工具**：自定义热键框选截图，自动存图/转格式/复制剪贴板，内置本地 OCR 识字（PaddleOCR-VL-1.6，支持常驻子进程模式，首次加载后续复用）。
- **⏺ 区域录屏**：Alt+2 定位/调整选区，再按 Alt+2（或点「开始录制」）开录；操作记录带视频缩略图，单击播放；依赖 ffmpeg。

### 助手 · 主手脚

- **📝 粘贴助手**：主题 + 内容 + 七色标签存为卡片，流式布局可拖拽排序，点击复制并载入编辑区；Ctrl+Alt+1 / 2 / 3 把选中文本加入 / 减去 / 互换复制内容；预下载记录也可一键转出为本页记录卡。
- **🖌️ 图片处理**：缩小 / 放大（Upscayl · Real-ESRGAN，2–6×）/ 切割 / 抠图（BiRefNet）/ 更多修改，底栏选格式保存；输出预览区可滚动查看历史结果，单击打开输出目录。
- **🎙 语音录入**：本地语音转文字（faster-whisper，子进程中加载模型，避免与 Qt 冲突崩溃）；录音与转写结果存 `VoiceEdit/`，也可上传音频直接转写；繁体输出自动转简体（`zhconv`）；记录卡可「跳转文本页」继续整理。
- **🎧 音频处理**（v9.17 新增）：AUK（降噪 / 改词 / 配音 / 改情绪 / 转耳语）+ CosVoice3（语音克隆）两个页签，共用声音卡与记录时间线；均在独立子进程中运行。
- **💰 积分计算**：按订阅费用（月/年，USD/CNY 汇率）、每月积分、单次消耗积分，折算单图/单秒视频的真实成本和月成本，历史可保存。
- **🎮 游戏助手**：导入攻略文本/文件建知识库，截图后本地 OCR 识字（可编辑）→ 检索知识库 → OpenAI 兼容大模型 API 作答，多个答案可切换。
- **🧠 游戏大脑**（v9.17 新增）：文本区内容直接送大模型 API（默认 OpenRouter + Jev 决策模型），结果写入「交互记录」；全程不抢焦点。
- **🤝 游戏会议**（v9.17 新增）：局域网房间发现 + 共享文档 + 编辑权勾选 + 历史数据；主机在本页且开启复制同步时，系统复制的内容写入公共区。
- **📐 比例计算**：比例换算、金额大写、汉字速查、公式计算、计算器。
- **📋 提示词**：文生视频提示词编辑，顶部分页（每页正文与预设分开存），左栏填写 / 右栏预设，字段化管理（基础参数 / 镜头 / 人物 / 体型 / 服装 / 装备 / 环境 / 场景细节 / 动作 / 表演 / 台词 / 色彩 / 画面质量）；预设来自 `docs/` 下两份 txt（见 v9.17 第 6 条）。

### 工具 · 小偏门

- **文本编辑**（v9.17 由语音编辑拆出）：主编辑区 + 一键整理 / 加序号 / 加符号前缀，前缀符号历史卡片可拖拽排序。
- **时区汇率**：多城市模拟时钟（可增删/可模拟时间）+ 汇率转换器。
- **目录映射**：图形化软链接创建，附磁盘空间 Treemap 可视化。

### 底栏 · 系统总览

环境信息（设备名 / 处理器 / 内存 / 系统 / Python / 磁盘）与资源监控（GPU 使用率、显存、内存、CPU、GPU 温度、GPU 功耗、硬盘；非 NVIDIA 显卡用 Windows 性能计数器兜底读 GPU 利用率）；「功能与说明」教程轮播（`assets/guides/*.webp`）；预下载域名白名单、组件安装；设置区：界面缩放、Cookie 设置、公共保存、预下载自动处理开关、数据管理（导出 / 导入 / 重置）。

### 顶栏入口（不占侧栏）

- **⏰ 电子钟**：点击顶栏时钟图标进入，或空闲一段时间后自动跳转；七段数码时分 + 月历 + 天气卡（天气界面先行，实况接口待接入）。

---

## 🛠️ 安装与运行

### A. 源码运行

> 仅支持 **Python 3.10 / 3.11 · Windows 10+**（部分能力仅限 Windows）。CosyVoice、PaddlePaddle 等依赖锁定了版本，其它 Python 版本装不上或运行出错；`1安装-组件.bat` 会先检查版本，不符合直接停止。
>
> 前置：**Git**（克隆 CosyVoice 源码要用）、**Deno**（YouTube JS 挑战要用）。两者第 1 步都会自动用 winget 安装（已在 PATH 就跳过，装不上只警告并给出手动命令）；也可以先手动装：`winget install Git.Git`、`winget install DenoLand.Deno`。

三步安装（推荐）：

```bat
1安装-组件.bat
2安装-模型.bat
3启动-程序.bat
```

1. **安装组件**：检查 Python 版本 → `requirements.txt` + ffmpeg / Deno / Git / torch / paddle / paddleocr[doc-parser] / CosyVoice 源码与依赖等（按本机 CUDA 选轮子），不下载大权重。
2. **安装模型**：交互选择，把开源模型下到 `model\`（PaddleOCR-VL-1.6、faster-whisper tiny/small/large-v3、Fun-CosyVoice3-0.5B）。`model/` 不进 Git。
3. **启动程序**：运行桌面助手（`pythonw mainv917.py`）。也可直接 `python mainv917.py`。

手动安装等价命令（先确认 `python --version` 是 3.10 或 3.11）：

```bat
winget install Gyan.FFmpeg
winget install DenoLand.Deno
winget install Git.Git
pip install -r requirements.txt
pip install -r requirements-models.txt
python tools\download_models.py
```

- `requirements-models.txt` 里 OCR 用的是 `paddleocr[doc-parser]>=3.6.0`（不带 `[doc-parser]` 跑不了 PaddleOCR-VL）；`numpy` 锁在 `<2`、`onnxruntime` 锁在 `<1.19`（CosyVoice 固定 1.18.0）。
- PyTorch、PaddlePaddle 请按本机 CUDA 另装，命令见 `requirements-models.txt` 注释。
- 两份 requirements 现在只含 ASCII 注释，中文 Windows 上**不再需要**设置 `PYTHONUTF8=1` 才能 `pip install -r`。
- winget 装完 ffmpeg / Deno / Git 后要**新开命令行窗口**，PATH 才生效。

装完组件后请**完全退出再开**；装过 ffmpeg 请**新开命令行窗口**再启动。

排障：

```bat
python tools\setup_components.py --check
python tools\diagnose_dll.py ocr
tools\fix_ocr.bat
```

`--check` 只检查不安装：会列出 ffmpeg、语音、OCR、CosyVoice、Deno、Git，以及下面几个手动组件的状态（手动组件缺了只提示，不算失败）。

#### 手动安装的可选组件

以下组件 `2安装-模型.bat` / `download_models.py` **不会下载**，需要自己准备文件放到下面的位置（路径相对项目根；编译版相对 exe 所在目录）。缺了只影响对应功能，其它功能照常。

| 组件 | 用在哪 | 程序查找的位置 |
|------|--------|----------------|
| **AuK** 权重 | 音频处理 · AUK 页签 | `model\AuK\auk_base.safetensors`、`model\AuK\config.yaml` |
| **AuK** 运行库 | 同上 | `model\AuK-src\src\`（里面是 `auk` 包，程序调用 `auk.infer.infer_auk`；启动子进程时把这个目录加进 `PYTHONPATH`） |
| **Qwen2.5-Omni-3B** | 同上（AuK 依赖） | `model\Qwen2.5-Omni-3B\`（完整模型目录，不能为空） |
| **BiRefNet-general** | 图片处理 · 抠图 | `model\BiRefNet-general\*.onnx`（只认大于 10 MB 的 `.onnx`，有多个时用最大的那个） |
| **Upscayl** | 图片处理 · 放大 | `model\upscayl\upscayl-bin.exe` + 模型 `model\upscayl\models\<模型名>.bin` / `.param`（也可直接放在 `model\upscayl\` 下；至少要有默认的 `realesrgan-x4plus`）；没有时回退到已安装的 Upscayl 桌面版 `C:\Program Files\Upscayl\resources\bin\upscayl-bin.exe` 及其 `resources\models` |

> ⚠️ **AuK 与 CosyVoice 的 transformers 冲突**：AuK 需要 `transformers 4.57`，CosyVoice 固定 `transformers 4.51.3`。AuK 子进程用的是主程序同一个 Python（`sys.executable`，只额外加了 `PYTHONPATH`），没有独立的包目录。曾为 AuK 把全局 transformers 升到 4.57，结果 CosyVoice 生成的声音变成乱声。二选一：
> - **推荐**：另建一个 venv（同样 Python 3.10 / 3.11）专门装 AuK 的依赖，需要用 AuK 时从这个环境启动桌面助手；语音克隆留在主环境里用。
> - **或者接受**：主环境保持 `transformers==4.51.3`，AuK 可能跑不起来。如果要在主环境装 4.57，语音克隆就会出问题，用完 AuK 要换回 4.51.3。

**关于管理员权限**：默认普通用户启动，需要时可显式提权 `python mainv917.py --as-admin`；目录映射页的 mklink 也可单独勾选管理员执行。

### B. 编译版（GitHub Releases）

编译好的 `mainv917.exe` 通过 GitHub Releases 发布，子进程以 `mainv917.exe --whisper-job / --ocr-job / --ocr-server / --cosyvoice-job / --auk-job / --matting-job` 方式拉起。发布目录要点：

| 路径 | 说明 |
|------|------|
| `mainv917.exe` | 主程序 |
| `_internal\` | PyInstaller 依赖；程序会在 `_internal\ffmpeg\bin\` 与 `_internal\deno\` 里查找 ffmpeg / Deno |
| `docs\` | **必须随 exe 一起发**：两份提示词预设 txt（程序先找 exe 旁 `docs\`，再回退打包资源目录与旧位置） |
| `model\` | 本地大模型权重（体积大，按需拷贝或用 `tools\download_models.py` 下载） |
| `data\` / `records\` 等 | 首次运行自动创建，**不要打包进发布** |

> 换电脑：整夹复制，不要只拷单个 exe。建议目标机安装 [VC++ Redistributable x64](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)。

**可选环境（源码与编译版通用）**
- **NVIDIA**：GPU 监控需 `nvidia-smi` 在 PATH（非 NVIDIA 显卡只读利用率）；OCR / 语音 / AuK 在有 GPU 时更快。
- **yt-dlp + yt-dlp-ejs + ffmpeg + Deno/Node.js**：视频下载 B站 / YouTube 子页、「其它视频」HLS 封装与区域录屏需要；`yt-dlp-ejs` 处理 JS 签名校验，建议随 `yt-dlp` 一同升级。
- **faster-whisper + sounddevice + soundfile**：语音录入转写需要，权重放 `model/faster-whisper-*`。
- **paddlepaddle + paddleocr[doc-parser]**：截图工具与游戏助手的 OCR 识字，权重放 `model/PaddleOCR-VL-1.6`（约 1.9GB）；异常可用 `tools/fix_ocr.bat` 修复。
- **CosyVoice3（可选，体积大）**：音频处理 · CosVoice3 页签需要，权重 `model/Fun-CosyVoice3-0.5B`（约 9GB），源码 `model/CosyVoice`（`python tools/download_models.py --only cosyvoice --only cosyvoice-code`）；PyTorch 按本机 CUDA 安装。
- **AuK（可选，需手动放置）**：音频处理 · AUK 页签需要 `model/AuK`（`auk_base.safetensors` + `config.yaml`）、`model/Qwen2.5-Omni-3B`、`model/AuK-src/src`；`download_models.py` 暂不包含这几项，transformers 版本与 CosyVoice 冲突，见上文「手动安装的可选组件」。
- **Upscayl / BiRefNet（可选，需手动放置）**：图片处理「放大」需要 `model/upscayl/upscayl-bin.exe` 及 `model/upscayl/models` 下的模型（或本机已装 Upscayl）；「抠图」需要 `model/BiRefNet-general` 下大于 10 MB 的 `.onnx`。详见上文「手动安装的可选组件」。
- **大模型 API**：游戏助手 / 游戏大脑需要一个 OpenAI 兼容服务（云端 API Key，或本机 Ollama / LM Studio）。
- **Get cookies.txt LOCALLY（浏览器扩展）**：导出 YouTube / e-hentai / hitomi.la / Pixiv 等站点 Cookie（Netscape 格式）。

### 🧩 浏览器扩展

速存图文的配套浏览器扩展**不在本仓库内**。扩展通过本机 WebSocket（`127.0.0.1:19876`，只接受 `chrome-extension://` / `moz-extension://` 来源）与桌面端通信；Chrome 加载方式：`chrome://extensions/` → 打开「开发者模式」→「加载已解压的扩展程序」。

### 🔑 配置

- **API Key**：仓库不含任何密钥。游戏大脑的 OpenRouter Key 在页面「API Key」里填写（优先），或设置环境变量 `OPENROUTER_API_KEY`；未设置时点「开工」会提示先填写 Key。
- **数据目录**：`data/`、`records/`、`VoiceEdit/`、`gameassist/`、`gamebrain/` 首次运行自动创建，属于个人数据，不要上传到仓库。
- **保存位置**：各功能的输出统一放在「公共保存」根目录下（默认系统「下载」文件夹），可在系统总览设置区修改。

---

## 📁 代码结构

```
桌面助手/
├─ mainv917.py                  # 入口（默认普通用户；--as-admin 显式提权；启动前设 QT_SCALE_FACTOR）
│                                 #   --whisper-job / --ocr-job / --ocr-server / --cosyvoice-job / --auk-job / --matting-job：
│                                 #   语音转写 / 截图 OCR（单次、常驻）/ 语音克隆 / AuK / 抠图 各自独立子进程
│                                 #   硬崩溃兜底：faulthandler → data/crash_*.log
├─ ui_main.py                   # 主窗口 + 侧栏三分区 + 拖拽排序 + 分区改名 + 底栏 + 顶栏（电子钟）
│                                 #   + 复制归属槽 + F7 静默复制
├─ requirements.txt / requirements-models.txt
├─ pages/
│  ├─ page_overview.py          # 系统总览（环境信息 + 资源监控 + 设置区 + 白名单/组件安装）
│  ├─ settings_section.py       # 设置区（缩放 / Cookie / 公共保存 / 预下载 / 数据管理 / 功能与说明）
│  ├─ page_about.py             # 组件安装 + 预下载域名白名单（嵌入系统总览）
│  ├─ pre_download_panel.py     # 预下载记录浏览/编辑弹窗
│  ├─ page_fast_save.py         # 速存图文
│  ├─ page_dup_clean.py         # 去除同名（速存图文内使用）
│  ├─ page_video.py             # 视频下载（Tab 容器 + 统一批量下载 + 预下载角标）
│  ├─ page_douyin.py / page_bilibili.py / page_youtube.py   # 三平台子页
│  ├─ page_other_video.py       # 其它视频（即梦 / LibTV / XVideos / Pornhub）
│  ├─ page_jimeng.py / page_liblib.py                       # 即梦 / LibTV 解析
│  ├─ page_audio.py             # 音频下载（汽水音乐）
│  ├─ page_gallery.py           # 图集下载（e-hentai/ExHentai + hitomi.la + Pixiv）
│  ├─ page_screenshot.py        # 截图工具（含 OCR 识字）
│  ├─ page_region_record.py     # 区域录屏
│  ├─ page_paste.py             # 粘贴助手
│  ├─ page_image_proc.py        # 图片处理（缩小/放大/切割/抠图/更多修改）
│  ├─ page_voice_input.py       # 语音录入（本地转写）
│  ├─ page_text_editor.py       # 文本编辑
│  ├─ page_auk.py               # 音频处理容器 + AUK 页签
│  ├─ page_cosvoice3.py / page_voice_clone.py               # CosVoice3 页签（语音克隆面板）
│  ├─ shared_log_panel.py       # 音频处理共用记录面板
│  ├─ page_gameassist.py        # 游戏助手
│  ├─ page_gamebrain.py         # 游戏大脑
│  ├─ page_gamemeet.py          # 游戏会议
│  ├─ page_prompt_editor.py     # 提示词编辑器
│  ├─ page_female_char.py       # 角色提示词母模板（代码内已实现，当前未挂入侧栏导航）
│  ├─ page_points_calc.py       # 积分计算
│  ├─ page_ratio_calc.py / page_dir_link.py / page_timezone_fx.py  # 比例计算 / 目录映射 / 时区汇率
│  ├─ page_clock.py             # 电子钟（顶栏入口 + 空闲跳转）
│  └─ disk_treemap_widget.py    # 磁盘空间 Treemap 可视化
├─ utils/                       # 通用模块（不依赖页面）
│  ├─ app_paths.py              # 统一路径（data / records / model / gameassist / 公共保存 + 旧布局迁移）
│  ├─ user_prefs.py             # 偏好持久化 data/user.txt（原子写入）
│  ├─ logger.py                 # 日志 data/app.log
│  ├─ download_confirm.py / download_watchdog.py   # 同名确认 / 下载防卡死（180s）
│  ├─ pre_download.py           # 预下载队列（11 个平台 + 无法处理），存 records/pre_download.json
│  ├─ gallery_records.py        # 图集已下记录
│  ├─ xvideos.py / pornhub.py   # 其它视频解析
│  ├─ ocr_util.py / ocr_job.py  # 本地 OCR（PaddleOCR-VL-1.6，含常驻服务模式）
│  ├─ voice_input.py / whisper_job.py          # 语音转写
│  ├─ cosyvoice_clone.py / cosyvoice_job.py    # 语音克隆
│  ├─ auk_client.py / auk_job.py               # AuK
│  ├─ matting_job.py            # 抠图（BiRefNet ONNX）
│  ├─ gameassist.py / gamebrain.py             # 知识库与大模型 API（不依赖 Qt）
│  ├─ gamemeet_net.py           # 游戏会议局域网同步
│  ├─ selection_capture.py      # F7 静默复制的前台选区捕获
│  ├─ clock_idle.py             # 电子钟空闲跳转
│  ├─ region_recorder.py        # 区域录屏核心
│  ├─ qthread_util.py           # 统一安全停止 QThread
│  ├─ flow_layout.py            # 流式布局
│  └─ cursor_toast.py           # 全局鼠标旁轻提示气泡
├─ styles/                      # style_all.py + side_nav_icons.py（侧栏矢量图标）
├─ assets/                      # app.qss / app_light.qss / 图标 / guides/*.webp（功能与说明）
├─ docs/                        # 分发说明 + jev_api.md（Jev 决策模型接入说明）
│                                 #   + 两份提示词预设 txt（发布时随 exe 一起带上）
├─ tools/                       # 安装 / 模型下载 / OCR 修复 / DLL 诊断 / hitomi 调试 / 打包瘦身 / 会议协议测试
│
│  # —— 运行时生成（不进仓库）——
├─ data/                        # user.txt / app.log / crash_*.log
├─ records/                     # 记录卡、图集已下、粘贴/积分、提示词、预下载队列等
├─ VoiceEdit/                   # 语音录入的录音与转写文本
├─ gameassist/ / gamebrain/     # 知识库索引
└─ model/                       # 本地大模型权重
```

> 主脑配置、导演台、Ollama 工具、反推生图等 AI 相关页面已从主程序拆出，将并入独立的「算力版」程序。

---

## ❗ 常见问题

- **`ModuleNotFoundError: No module named 'utils'`** → 从项目根目录运行 `python mainv917.py`。
- **资源监控无 GPU 数据** → 安装 `psutil`；显存/温度/功耗需 NVIDIA 且 `nvidia-smi` 在 PATH，其它显卡只显示利用率。
- **语音录入转写无反应** → 需安装 `faster-whisper` + `sounddevice`，并下载 `model/faster-whisper-*`；转写在独立子进程中加载，失败时请查看 `data/app.log`。
- **B站 / YouTube 无法解析 / 提示缺 ffmpeg 或 Deno** → 安装 `yt-dlp`（建议同时装 `yt-dlp-ejs`）、ffmpeg、Deno/Node；编译版请确认 `_internal\ffmpeg\bin\` 与 `_internal\deno\` 存在或系统 PATH 里有。
- **区域录屏提示找不到 ffmpeg** → `winget install Gyan.FFmpeg` 后新开窗口再启动。
- **图集下载访问 ExHentai / hitomi.la / Pixiv 失败** → 需导入有效 Netscape 格式 Cookie；hitomi.la 可用 `tools/hitomi_zip_debug.py` 单独核实；Pixiv 需先在站内登录后导出 Cookie。
- **图集「已下记录」删了又回来** → 记录会扫描保存目录里已有的作品文件夹/zip 自动补全；要清空需先移走对应作品或改用空目录。
- **下载一直卡在某一条不动** → 防卡死看门狗 180 秒无新数据会自动判定失败并跳过；若仍长时间无响应，请检查网络或站点是否失效。
- **下载提示「已存在同名文件」** → 弹窗默认 10 秒后自动中止，也可手动选择改名或覆盖。
- **预下载队列不自动继续** → 确认系统总览「预下载」开关（或侧栏底栏「预」）已打开；本机未加载对应 Cookie 时启动会警告并自动关闭；也可点「预处理文件」手动查看/编辑队列。
- **调整界面缩放后重启没生效** → 检查 `data/user.txt` 里 `ui.scale` 是否写入正确。
- **速存图片插件状态灯黄/红** → 黄=等待连接或心跳超时；红=端口占用或 Chrome 未运行，确认安全软件未拦截本机 19876 端口。
- **截图 OCR 无法识字** → 需安装 `paddlepaddle`（或 `paddlepaddle-gpu`）与 `paddleocr[doc-parser]`，并把 `PaddleOCR-VL-1.6` 权重放到 `model/`；可运行 `tools/fix_ocr.bat`，或在「截图工具」内点「检测」。
- **CosVoice3 没反应 / 提示缺权重** → 需要 `model/Fun-CosyVoice3-0.5B` 与 `model/CosyVoice` 源码；参考原声超过 30 秒会被裁剪。
- **AUK 提示「还缺：…」** → 按提示把 AuK 权重、`model/AuK-src`、`model/Qwen2.5-Omni-3B` 放齐（见「手动安装的可选组件」）；显存不够可打开「省显存」。不要为了 AuK 在主环境升级 transformers，否则语音克隆会出乱声。
- **`1安装-组件.bat` 提示 Python 版本不对** → 只支持 3.10 / 3.11，装好后新开窗口再运行；装了多个版本时让 PATH 里的 `python` 指向 3.10 / 3.11。
- **第 1 步 Deno / Git 装不上** → 不影响其它组件；手动执行 `winget install DenoLand.Deno` / `winget install Git.Git`，新开窗口后运行 `python tools\setup_components.py --check` 确认。
- **图片处理「放大」提示缺少程序** → 把 `upscayl-bin.exe` 与模型放到 `model/upscayl`，或安装 Upscayl 桌面版。
- **游戏大脑提示「还没有 API Key」** → 在页面「API Key」里填写 OpenRouter 密钥，或设置环境变量 `OPENROUTER_API_KEY` 后重启。
- **游戏助手连不上模型 / 检索无结果** → 检查「大模型 API」的地址、Key 与模型名（可点「检测」拉取模型列表）；用本机 Ollama / LM Studio 时先启动服务；知识库为空时先导入攻略文本/文件。
- **按 F7 没反应 / 和游戏冲突** → 确认静默复制已开启、侧栏复制归属指向目标页（游戏会议需当前停在该页）；与游戏热键冲突时关掉静默复制即可让出 F7。
- **提示词页没有 H3 按钮 / 预设很少** → 确认 `docs/MiniMAX_H3_提示词分类.txt` 与 `docs/内置预设_提示词分类.txt` 存在（编译版放 exe 旁 `docs\`）。
- **换电脑后缺 DLL / 子进程起不来** → 安装 VC++ x64 运行库；保持 `_internal`、`docs`、`model` 与 exe 同目录整夹复制。
- **找不到「关于」页** → v9.17 起并入底栏「系统总览」；电子钟不在侧栏，点击顶栏时钟图标进入。

---

## ✅ 开发规范

- **样式与逻辑分离**：视觉样式写入 `assets/*.qss` 与 `styles/style_all.py`；布局与业务逻辑写在 Python。
- **优雅降级**：`psutil` / `nvidia-smi` / `yt-dlp` / `faster-whisper` / `paddleocr` / CosyVoice / AuK / 大模型 API 不可用时提示但不崩溃。
- **Windows 依赖隔离**：`pywin32`、WinAPI 相关代码均有 `try/except` 保护。
- **子进程隔离**：OCR、语音转写、语音克隆、AuK、抠图等易与主进程 Qt 抢占 DLL 的能力，统一放到独立子进程（`--ocr-job` / `--ocr-server` / `--whisper-job` / `--cosyvoice-job` / `--auk-job` / `--matting-job`）执行，且必须在 import PyQt5 之前退出分支。
- **不动旧功能的依赖**：新功能缺的包装进自己的子进程，不升级主环境里旧功能正在用的公共包。
- **不抢焦点**：游戏大脑 / 游戏会议 / F7 静默复制全程不 `activateWindow` / `raise_`，不 `grabKeyboard`，不弹模态框。
- **后台线程与关窗清理**：涉及定时器/`QThread` 的页面需在 `ui_main.py` 的 `closeEvent` 统一停止，见 `utils/qthread_util.py`。
- **持久化原子写入**：用户配置「写 `.tmp` + `os.replace`」，见 `utils/user_prefs.py`。
- **数据与模型分层**：程序运行数据进 `data/`，用户创造的数据进 `records/`；大体积模型权重进 `model/`；知识库单独放 `gameassist/` / `gamebrain/`。
- **不提交密钥与个人数据**：API Key 只来自页面输入或环境变量；运行时目录不上传到仓库。

---

## 📜 版本历史

| 版本 | 主要内容 |
| --- | --- |
| **v9.17 (当前)** | 重新开源（MIT）；新增「音频处理」（AUK 降噪/改词/配音/改情绪/转耳语 + CosVoice3 语音克隆两页签，独立子进程）、「音频下载」（汽水音乐）、「游戏大脑」（OpenRouter + Jev）、「游戏会议」（局域网共享文档）、「文本编辑」；视频下载新增「其它视频」（即梦 / LibTV / XVideos / Pornhub）；游戏助手改为 OpenAI 兼容 API；全局 F7 静默复制 + 侧栏复制归属；提示词预设外置到 `docs/` txt（内置 79 + H3 1223，前 15 条 + H3 折叠，最近使用排序）；图片处理新增放大（Upscayl）与抠图（BiRefNet）；运行数据拆为 `data/` + `records/`；系统总览移到底栏并并入「关于」；电子钟空闲跳转；功能说明图改 WebP；API Key 改为页面填写或 `OPENROUTER_API_KEY`；入口 `mainv917.py`。 |
| **v9.16** | 新增「语音克隆」（本地 CosyVoice3-0.5B，独立子进程）、「游戏助手」（知识库+截图+Ollama 检索问答，多候选答案）；截图 OCR 由 RapidOCR 换为 PaddleOCR-VL-1.6，新增常驻子进程模式；运行数据统一进 `data/`，新增 `model/`；入口 `mainv916.py` / 便携 `mainv916.exe`，崩溃日志兜底；便携包内置 `runtime` Python 与 `_internal` 下的 ffmpeg、Deno，可用 `启动.bat` 整夹迁移。 |
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

---

## 📄 许可证

[MIT](LICENSE)
