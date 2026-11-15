import re

from .errors import ValidationError


def validate_summary(text):
    body = str(text or '').strip()
    if len(body) < 20:
        raise ValidationError('没有足够的摘要正文；空回复或执行承诺不能作为结果。')
    if re.fullmatch(r'(?:好的[，,。]?\s*)?(?:我会|我将|我来|接下来|正在|准备|让我).{0,100}', body, re.S) and len(body) < 120:
        raise ValidationError('返回了执行承诺，未提供实际摘要正文。')
    if '<tool_call>' in body or body.startswith('<think>'):
        raise ValidationError('返回了工具调用或思考过程，未提供可验收正文。')
    return body
