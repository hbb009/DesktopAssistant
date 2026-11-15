# Agent v2 第一阶段：持久化文档任务核心

本阶段增加独立的 TXT/Markdown 分段摘要任务入口，不替换原聊天页面，也不宣称已经实现风格 Skill、大纲或小说写作。它用于验证后续五类工作流共用的存储、调度、模型请求和恢复机制。

## 当前可用

- 明确指定一份或多份文档，冻结输入清单及正文缓存。
- 单任务、单模型请求串行执行；默认固定8192上下文、1024输出预算。
- 按段落切分，保存字符与UTF-8字节位置；不会同时将全部文档送给模型。
- 模型只返回本步摘要正文；程序决定目标文件、校验与保存，不依赖模型调用保存工具。
- 每步记录输入、请求、原始流、正文、统计、检查结果、成功提交记录。
- 暂停和重新运行；已提交且有效的步骤不会重复调用模型。
- 损坏或缺失的中间产物重新生成；原始输入改变时停止，要求明确新建任务，避免混用两个版本。
- 最终摘要保存在任务的 `outputs/` 中；如果用户修改了已发布摘要，程序不自动覆盖。
- OS文件锁防止同一任务被两进程写入；同一数据目录内任务串行调度。

摘要按片段顺序汇集，尚未做全篇语义综合；不能把它当作完整小说风格提炼功能。

## 在 PowerShell 中使用

在项目根目录 `D:\Desktop Assistant` 打开终端，使用程序原本的Python环境。

创建任务（此步不调用模型）：

```powershell
python tools/agent_v2.py create --input 'D:\材料\说明.txt' 'D:\材料\记录.md' --model 'qwen3.6-jailbreak:q6'
```

返回一个完整任务目录。运行该任务：

```powershell
python tools/agent_v2.py run '上一步返回的任务目录'
```

运行中按 Ctrl+C 请求暂停。也可在另一个终端执行：

```powershell
python tools/agent_v2.py pause '任务目录'
python tools/agent_v2.py inspect '任务目录'
```

恢复仍使用同一个 `run` 命令。可以先运行一个小步骤检查效果：

```powershell
python tools/agent_v2.py run '任务目录' --max-steps 1
```

`--max-steps` 是本次新增成功步骤的上限，包含确定性的合成步骤。达到上限后暂停；最后一项完成但尚未发布时再次运行会完成发布。

创建时可选参数：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--instruction` | 提取重要事实与要点，不添加原文没有的信息 | 完整保留用户要求 |
| `--data-dir` | 项目 `userdata/agent_tasks` | 任务存储目录，应放在可写且空间足够的磁盘 |
| `--chunk-chars` | 1600 | 每片段字符上限，按段落调整边界 |
| `--num-ctx` | 8192 | 固定上下文，不自动放大，第一版范围2048～32768 |
| `--num-predict` | 1024 | 输出token上限 |
| `--read-timeout` | 120 | 模型每次网络读取超时秒数，冷启动较慢时可设300 |
| `--encoding` | utf-8-sig | GB编码文本可显式设置gb18030，不静默猜测 |
| `--base-url` | http://127.0.0.1:11434 | 本地Ollama地址；第一阶段不支持认证密钥 |

单文件读取上限32MB。只处理明确指定的TXT/Markdown，不递归扫描目录，不删除或改写原始文档。

## 每一步的文件

```text
task.json / plan.json / state.json
manifests/sources.json
manifests/artifacts.json
inputs/doc-0001.txt
steps/doc-0001-chunk-00001/
  input.json
  attempts/0001/
    request.json
    stream.jsonl
    response.part
    response.txt
    metadata.json
    validation.json
    output.txt
  result.json
  commit.json
outputs/doc-0001-summary.md
logs/events.jsonl
```

`response.part` 只是中间结果；收到正常结束标记并通过校验后，才生成并提交有效产物。异常输出保留用于排查，不会冒充最终摘要。`commit.json` 绑定输入指纹和产物哈希；恢复不能只看 `state.json` 的成功字样。

不要在任务运行中编辑内部文件。任务目录中保存了实际请求和用户材料，诊断时不要无意分享整个目录。

## 测试和真实模型验收

```powershell
python -m unittest discover -s tests/agent_v2 -p 'test_*.py' -v
python tools/agent_v2_probe.py --model 'qwen3.6-jailbreak:q6' --output '一个诊断目录'
python tools/agent_v2_acceptance.py --case documents --workspace '一个尚不存在的测试目录' --read-timeout 300
```

自动化测试包含假HTTP服务和进程终止测试，均使用临时合成材料。真实模型验收会创建两份合成文档，执行一个步骤后暂停，重新从磁盘打开任务并完成，验证检查点未重写、两份输出存在、原件未改动。

真实模型验收状态必须以生成的 `acceptance-report.json` 为准。程序测试通过不能替代真实模型验收。模型不能运行或持续超时时，任务保留为失败/暂停，不进行虚假完成。

## 本阶段限制与下一阶段

- 当前入口为命令行，原聊天页继续使用旧Agent；重启程序不会自动切换到新核心。
- 还未接入网页助手、DOCX/PDF、通用批量文件操作、风格Skill、大纲和逐章确认界面。
- 本阶段输入改变时明确要求新建任务；按依赖自动重规划属于后续阶段。
- 同一数据目录内串行执行；不要用不同数据目录并发启动大模型任务。
- Windows暂停时不等待阻塞的网络读取结束；接收线程只处理网络事件、没有文件写入权限。旧响应不会提交任务结果；服务端停止生成的具体速度取决于模型服务。
- 下一步优先把任务列表、可读进度、暂停/恢复接入桌面界面，再逐项接入其余工作流。
