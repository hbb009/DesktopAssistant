import json
import math

from .errors import NeedsInput


def estimate_tokens(text):
    # Deliberately conservative; actual token counts are retained for calibration.
    return math.ceil(sum(1.5 if ord(c) > 127 else 0.4 for c in str(text)))


def check_budget(messages, config):
    estimated = estimate_tokens(json.dumps(messages, ensure_ascii=False))
    margin = max(256, config.num_ctx // 10)
    if estimated + config.num_predict + margin > config.num_ctx:
        raise NeedsInput(f'输入预算不足：估计输入{estimated}，预留输出{config.num_predict}，窗口{config.num_ctx}。请减小片段或任务要求后新建任务；程序不会自动增大窗口。')
    return {'estimated_input_tokens': estimated, 'output_reserve':config.num_predict, 'margin':margin, 'num_ctx':config.num_ctx}
