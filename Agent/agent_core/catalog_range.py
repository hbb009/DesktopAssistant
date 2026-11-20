"""Read explicit chapter ranges without trusting a model's default count."""
import re
from .errors import NeedsInput


def chapter_range(instruction):
    # Only the original user request, never the model's appended paraphrase.
    text = instruction.split('整理后的要求：', 1)[0]
    match = re.search(r'(?:第\s*)?(\d+)\s*(?:章|节|回)?\s*[-—–~～至到]\s*(?:第\s*)?(\d+)\s*(?:章|节|回)', text)
    if not match:
        return None
    first, last = map(int, match.groups())
    if first < 1 or last < first:
        raise NeedsInput('章节范围无效，请提供起始章和结束章。')
    return first, last
