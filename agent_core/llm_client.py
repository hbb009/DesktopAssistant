import http.client
import json
from pathlib import Path
import socket
import time
from urllib.parse import urlsplit

from .errors import Cancelled, NeedsInput, OutputTruncated, RetryableError
from .models import ModelConfig
from .store import atomic_bytes, atomic_json
from .transport import network_events


class OllamaClient:
    def __init__(self, config):
        self.config = config
        parsed = urlsplit(config.base_url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Ollama地址须为不含认证信息的HTTP(S)地址。')
        self.url = parsed

    def generate(self, request, attempt, cancellation):
        cancellation.check()
        attempt = Path(attempt)
        attempt.mkdir(parents=True, exist_ok=True)
        payload = {key:value for key,value in request.items() if not key.startswith('_')}
        atomic_json(attempt/'request.json', request)
        started = time.monotonic()
        meta = {'model':self.config.model,'num_ctx':self.config.num_ctx,'num_predict':self.config.num_predict}
        content, done = [], False

        try:
            with (attempt/'response.part').open('wb') as body, (attempt/'stream.jsonl').open('wb') as raw:
                for event, value in network_events(self.url,self.config.read_timeout,payload,cancellation):
                    cancellation.check()
                    if event=='status':
                        meta['http_status']=value
                        continue
                    if event=='http_error':
                        status,detail=value
                        atomic_bytes(attempt/'error-response.txt',detail.encode('utf-8'))
                        error=f'HTTP {status}: {detail[:2000]}'
                        if status in (429,500,502,503,504):
                            raise RetryableError(error)
                        raise NeedsInput(error)
                    line=value
                    raw.write(line)
                    raw.flush()
                    try:
                        item = json.loads(line)
                        if not isinstance(item,dict):
                            raise ValueError('expected object')
                    except (ValueError,UnicodeError) as exc:
                        raise RetryableError('模型服务返回了不完整的JSON流。') from exc
                    if item.get('error'):
                        raise RetryableError('模型服务错误：' + str(item['error'])[:2000])
                    piece = str((item.get('message') or {}).get('content') or '')
                    if piece:
                        if 'first_token_seconds' not in meta:
                            meta['first_token_seconds'] = time.monotonic()-started
                        content.append(piece)
                        body.write(piece.encode('utf-8'))
                        body.flush()
                    if item.get('done'):
                        done = True
                        for key in ('done_reason','prompt_eval_count','eval_count','load_duration','total_duration','eval_duration'):
                            if key in item:
                                meta[key] = item[key]
                        break
            cancellation.check()
            if not done:
                raise RetryableError('模型连接提前结束，未收到完成标记；未将半截正文作为结果。')
            if meta.get('done_reason') == 'length':
                raise OutputTruncated('模型输出达到本步长度上限，未将截断内容记为完成。')
            text = ''.join(content)
            atomic_bytes(attempt/'response.txt', text.encode('utf-8'))
            return text
        except (OSError,http.client.HTTPException) as exc:
            cancellation.check()
            meta['error'] = str(exc)
            raise RetryableError('Ollama连接失败或响应超时：' + str(exc)) from exc
        except Exception as exc:
            cancellation.check()
            meta['error'] = str(exc)
            raise
        finally:
            meta['elapsed_seconds'] = time.monotonic()-started
            atomic_json(attempt/'metadata.json',meta)
