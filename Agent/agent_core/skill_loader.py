"""Load directory skills and legacy Markdown skills without escaping the package."""
from pathlib import Path
from .errors import NeedsInput


def skill_files(path):
    if not path:
        return []
    path=Path(path).resolve()
    if path.is_file():
        if path.suffix.lower() not in ('.md','.markdown'):
            raise NeedsInput('Skill入口必须是Markdown文件。')
        return [path]
    if not (path/'SKILL.md').is_file():
        raise NeedsInput('Skill目录缺少SKILL.md。')
    files=[path/'SKILL.md']
    ref=path/'references'
    if ref.exists():
        for file in sorted(ref.rglob('*.md')):
            if not file.resolve().is_relative_to(path):
                raise NeedsInput('Skill参考文件不能指向目录之外。')
            files.append(file)
    return files
