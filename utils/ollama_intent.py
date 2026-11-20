# utils/ollama_intent.py
# 对话页「听话」模式：先看用户这一句要干什么，再决定给哪些工具。
# 纯函数，不碰 Qt / 网络。开关关掉时走 legacy，工具仍按联网/工作目录全开。

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import re

from utils.ollama_auto import SAVE_RE, find_url
from utils.ollama_chunk import _TASK_RE as TASK_RE
from utils.ollama_agent import AGENT_SYSTEM

WEB_TOOLS = ("web_search", "web_fetch")
# 阅读任务同时开放持久笔记的写入和读取，支持压缩及恢复。
DIR_TOOLS = ("list_articles", "read_article", "save_article", "save_notes", "read_notes")
ALL_TOOLS = WEB_TOOLS + DIR_TOOLS

_FILE_NAME = r"[\u4e00-\u9fffA-Za-z0-9_\-]+\.(md|txt|markdown)(?![A-Za-z0-9])"
# 纯列出目录请求（如「列出工作目录」「有哪些文件」）
LIST_ONLY_RE = re.compile(
    r"^\s*(列出|查看|看看)?(工作)?目录(里|中)?(有哪些文件|的文件|清单)?\s*[？?。!！]*$|"
    r"列出|有哪些文件|目录里有|工作目录",
    re.I,
)
FILE_RE = re.compile(
    r"列出|有哪些文件|目录里有|工作目录|文件夹|工作区|"
    r"(读一下|读取|打开|看看|看一下|读读|查看|检查|分析|梳理|汇总)\s*" + _FILE_NAME + r"|"
    + _FILE_NAME + r".{0,12}(读|打开|看|分析|检查|改|写)|"
    r"第[0-9０-９一二三四五六七八九十百千零〇两]+[章节回卷部篇集]|"
    r"(读|看|分析|检查|阅读|梳理|汇总).{0,12}(章节|文章|小说|文件|文档|内容|笔记|设定|伏笔|人物|剧情|风格)|"
    r"(里面|目录里|文件夹里).{0,12}(的|有什么|写了什么|讲了什么|内容|文章|小说|章节)",
    re.I,
)
WRITE_RE = re.compile(
    r"写一篇|写一章|写一段|写个|写篇|写一份|起草|创作|编一|生成|输出|整理成|写成|编写|"
    r"skill|技能|风格指南|style-guide|references",
    re.I,
)
WEB_RE = re.compile(
    r"搜索|搜一下|搜搜|查一下|查查|上网搜|网上搜|联网搜|"
    r"今天天气|今日天气|新闻|股价|汇率|比分|金价|现在几点"
)
FACT_Q_RE = re.compile(
    r"(今天|现在|目前|最近|最新).{0,16}(谁|什么|多少|几|哪|是否|有没有|怎么了)|"
    r"(谁赢了|比分多少)"
)
HAS_FILE_RE = re.compile(_FILE_NAME, re.I)

# 只改文件名、不读正文的任务（整理文件名 / 重命名 / 只保留汉字和数字）
RENAME_RE = re.compile(
    r"重命名|改文件名|整理文件名|文件名整理|"
    r"(改|换|整理|修改)(一下|下)?(文件)?名|"
    r"文件名.{0,16}(整理|只要|只留|只保留|仅保留|只含|只要留)|"
    r"(只要|只留|只保留|仅保留|只含).{0,12}(汉字|中文).{0,8}(数字)?|"
    r"文件名只(要|留|保留|含)"
)
NO_READ_RE = re.compile(
    r"不要读(取)?((文件)?内容|正文)|"
    r"不用读(取)?((文件)?内容|正文)|"
    r"不必读(取)?((文件)?内容|正文)|"
    r"别读(取)?((文件)?内容|正文)|"
    r"不读(取)?((文件)?内容|正文)|"
    r"不要看(文件)?内容|不必看内容|不用看内容"
)
WANT_CONTENT_RE = re.compile(r"读(取|一下|一读)?(文件)?(内容|正文)|看(一下)?(文件)?(内容|正文)|总结内容|改写内容|修改正文")

RENAME_SYSTEM = (
    "这是整理文件名的任务：只改名字，不要读取或改写文件内容，不要调用 read_article 或 save_article。\n"
    "先调用 list_articles 查看清单，再调用 rename_article。\n"
    "filename 或 index 指定原文件；new_filename 可省略（省略时程序只保留原名中的汉字和数字，并保留扩展名）。\n"
    "也可传入 renames 列表，或 mode=\"han_digits\" 批量处理清单中的文件。目标名若已存在，程序不会覆盖已有文件。"
)


def is_rename_only_task(text: str) -> bool:
    """用户要整理/重命名文件名，并且没有明确要求读或改正文。"""
    t = text or ""
    if not t.strip():
        return False
    if not RENAME_RE.search(t):
        return False
    if NO_READ_RE.search(t):
        return True
    if WANT_CONTENT_RE.search(t):
        return False
    return True


OBEY = (
    "只做用户这一句明确要求的事。"
    "没说保存就不要调用 save_article。"
    "没说搜索或打开网页就不要调用 web_search、web_fetch。"
    "不确定就先问一句。用简体中文直接回答。"
)
OBEY_CHAT = (
    "普通对话：直接用简体中文回答。"
    "不要搜索，不要保存文件，不要假装已经做了这些事。"
)
OBEY_LOOK = (
    "用户贴了网址。程序已经打开这一页，正文和链接在消息里，只在对话中，没有存进工作目录。"
    "根据这些内容回答他问的问题（这一页是什么、看到了什么、有哪些章节）。"
    "不要说已经保存，也不要调用 save_article。"
    "要打开链接列表里的其他地址时，调用 web_fetch。"
)
OBEY_LOOK_SAVE = (
    "用户贴了网址。程序已经打开这一页，正文和链接在消息里，目录页本身不要存成文件。"
    "先 web_fetch 打开他点名的章节或链接，读到正文后再 save_article。"
    "filename 只写文件名。没读到那一章就不要假装已经保存。"
)


@dataclass(frozen=True)
class Route:
    name: str
    label: str
    tools: tuple
    think: Optional[bool]
    temperature: Optional[float]
    system: str

    def uses_web(self) -> bool:
        return any(t in self.tools for t in WEB_TOOLS)

    def uses_dirs(self) -> bool:
        return any(t in self.tools for t in DIR_TOOLS + ("save_notes", "rename_article", "read_unread_batch"))


def folder_system(folder: str, tools: tuple) -> str:
    """工作目录说明：只在本轮真的给了目录工具时使用。与 AGENT_SYSTEM 保持完全一致，绝不自相矛盾。"""
    save = "save_article" in tools
    read = any(n in tools for n in ("read_article", "read_unread_batch", "list_articles"))
    rename = "rename_article" in tools
    if not save and not read and not rename:
        return ""
    where = (folder or "").strip() or "（还没有选择）"
    parts = [f"工作目录：{where}。filename 只写相对路径（可含子目录、空格和标点）。"]
    if rename and not save and not any(n in tools for n in ("read_article", "read_unread_batch")):
        parts.append("这是整理文件名：只调用 list_articles 和 rename_article，不要读取或改写正文。")
        return "".join(parts)
    if read and not save:
        parts.append("可调用 list_articles 查看清单、read_article（传 index 或完整 filename）读取正文。")
    if rename:
        parts.append("若只需改文件名，调用 rename_article。")
    if save:
        parts.append(
            "保存文件时调用 save_article：可以直接把完整正文放进 content 参数，或者先在回复里写出正文（或代码块）再调用 save_article(filename=...)。以工具返回「已保存：」为准。"
        )
    return "".join(parts)


def _unique(names) -> tuple:
    seen = set()
    out = []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return tuple(out)


def classify(text: str, *, web: bool = False, dirs: bool = False, obedient: bool = True) -> Route:
    """根据最后一句用户话决定本轮工具、是否思考、温度。"""
    t = text or ""
    if not obedient:
        if dirs and is_rename_only_task(t):
            tools = ("list_articles", "rename_article", "save_notes")
            return Route("agent", "本轮：整理文件名", _unique(tools), False, 0.3, AGENT_SYSTEM + "\n" + RENAME_SYSTEM)
        names = []
        if web:
            names.extend(WEB_TOOLS)
        if dirs:
            names.extend(DIR_TOOLS)
        return Route("agent", "本轮：Agent 循环", _unique(names), False, 0.3, AGENT_SYSTEM)

    want_rename = bool(dirs and is_rename_only_task(t))
    want_file = bool(dirs and (FILE_RE.search(t) or HAS_FILE_RE.search(t)))
    want_task = bool(dirs and TASK_RE.search(t))
    want_write = bool(dirs and (SAVE_RE.search(t) or WRITE_RE.search(t)))
    want_web = bool(web and (WEB_RE.search(t) or FACT_Q_RE.search(t)))
    if want_rename:
        tools = ("list_articles", "rename_article", "save_notes")
        system = AGENT_SYSTEM + "\n" + RENAME_SYSTEM
        return Route("agent", "本轮：整理文件名", _unique(tools), False, 0.3, system)
    # 「今天写一篇日记」走写作，不因为「今天」去搜网页。
    if want_write and not WEB_RE.search(t) and not FACT_Q_RE.search(t):
        want_web = False

    has_url = bool(find_url(t))
    needs_agent = has_url or want_web or want_file or want_write or want_task
    if needs_agent:
        tools = []
        if web and (want_web or has_url):
            tools.extend(WEB_TOOLS)
        elif has_url:
            tools.append("web_fetch")
        if dirs:
            if want_file or want_task:
                tools.extend(("list_articles", "read_article", "save_notes", "read_notes"))
            if want_write or want_task or (has_url and SAVE_RE.search(t)):
                tools.append("save_article")
        label = "本轮：Agent 循环"
        if has_url and not SAVE_RE.search(t):
            label = "本轮：Agent（看网页，未要求保存）"
        elif has_url:
            label = "本轮：Agent（打开链接再保存）"
        return Route("agent", label, _unique(tools), False, 0.3, AGENT_SYSTEM)

    return Route("chat", "本轮：闲聊（未开放工具）", (), False, 0.4, OBEY_CHAT)
