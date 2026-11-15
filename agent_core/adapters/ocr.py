import json
import subprocess
import sys
import time
from pathlib import Path
from ..errors import NeedsInput
from ..store import atomic_json

def recognize(image,cache,token=None):
    from utils.app_paths import app_root
    root=Path(app_root())
    cache=Path(cache); cache.mkdir(parents=True,exist_ok=True)
    result=cache/'result.json'; meta=cache/'request.json'
    atomic_json(meta,{'action':'ocr','image':str(image),'out':str(result),
                      'model_dir':str(root/'model'/'PaddleOCR-VL-1.6')})
    command=[sys.executable,'--ocr-job',str(meta)] if getattr(sys,'frozen',False) else [sys.executable,'-m','utils.ocr_job',str(meta)]
    with (cache/'process.log').open('wb') as log:
        process=subprocess.Popen(command,cwd=str(root),stdout=log,stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+300
            while process.poll() is None:
                if token: token.check()
                if time.monotonic()>deadline: raise NeedsInput('OCR识别超时，已保存页面和日志：'+str(cache))
                time.sleep(.1)
        finally:
            if process.poll() is None: process.kill(); process.wait()
    if not result.exists(): raise NeedsInput('OCR未生成结果，请查看：'+str(cache/'process.log'))
    data=json.loads(result.read_text(encoding='utf-8'))
    text=data.get('text','').strip()
    if not data.get('ok') or not text or text=='（未识别到文字）':
        raise NeedsInput('OCR无法识别本页：'+data.get('error','没有文字')+'；日志：'+str(cache))
    return text
