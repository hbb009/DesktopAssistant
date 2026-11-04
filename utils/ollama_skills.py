# utils/ollama_skills.py
# 对话技能：skills 目录里的 .md。勾选后把正文注入系统提示，不改磁盘上的文章目录。

from __future__ import annotations

import os
import re

_APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def skills_dir() -> str:
    path = os.path.join(_APP_ROOT, "skills")
    os.makedirs(path, exist_ok=True)
    return path


def parse_skill_text(text: str, fallback_name: str) -> dict:
    name, desc = fallback_name, ""
    stripped = (text or "").lstrip("\ufeff").lstrip()
    if stripped.startswith("---"):
        lines = stripped.splitlines()
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is not None:
            for ln in lines[1:end]:
                if ":" in ln or "：" in ln:
                    key, val = re.split(r"[:：]", ln, 1)
                    key = key.strip().lstrip("#").strip().lower()
                    val = val.strip().strip("\"'")
                    if key in ("name", "名称") and val:
                        name = val
                    elif key in ("description", "desc", "描述") and val:
                        desc = val
            body = "\n".join(lines[end + 1:]).strip()
            return {"name": name, "desc": desc, "prompt": body or stripped}
    return {"name": name, "desc": desc, "prompt": stripped}


def scan_skills() -> list:
    folder = skills_dir()
    try:
        files = sorted(
            f for f in os.listdir(folder)
            if f.lower().endswith((".md", ".markdown"))
        )
    except OSError:
        return []
    skills = []
    seen = set()
    for fn in files:
        path = os.path.join(folder, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            try:
                with open(path, "r", encoding="gb18030") as f:
                    raw = f.read()
            except OSError:
                continue
        sk = parse_skill_text(raw, os.path.splitext(fn)[0])
        if not (sk.get("prompt") or "").strip():
            continue
        if sk["name"] in seen:
            continue
        sk["file"] = fn
        sk["path"] = path
        skills.append(sk)
        seen.add(sk["name"])
    return skills


def compose_skills(skills: list) -> str:
    if not skills:
        return ""
    blocks = [
        "你已加载以下技能（Skill）。请在合适的场景使用它们完成用户的任务。",
    ]
    for i, sk in enumerate(skills, 1):
        block = f"技能 {i}：{sk.get('name') or '未命名'}"
        desc = (sk.get("desc") or "").strip()
        if desc:
            block += f"\n用途：{desc}"
        block += "\n" + (sk.get("prompt") or "").strip()
        blocks.append(block)
    return "\n\n".join(blocks)
