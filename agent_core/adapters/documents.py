from pathlib import Path

from ..errors import NeedsInput


MAX_SOURCE_BYTES = 32 * 1024 * 1024


def read_document(path, encoding='utf-8-sig', cache_dir=None, cancellation=None):
    path = Path(path).resolve()
    if path.suffix.lower() not in ('.txt', '.md', '.markdown', '.docx', '.pdf') or not path.is_file():
        raise NeedsInput('请选择TXT、Markdown、DOCX或PDF文件：' + str(path))
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise NeedsInput('文件超过第一阶段单文件32MB限制，请先拆分：' + str(path))
    with path.open('rb') as stream:
        raw = stream.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise NeedsInput('读取期间文件超出32MB限制：' + str(path))
    try:
        if path.suffix.lower()=='.docx':
            from docx import Document
            from io import BytesIO
            doc=Document(BytesIO(raw))
            text='\n'.join([p.text for p in doc.paragraphs]+[' | '.join(c.text for c in r.cells) for table in doc.tables for r in table.rows])
        elif path.suffix.lower()=='.pdf':
            import pymupdf
            with pymupdf.open(stream=raw,filetype='pdf') as doc:
                pages=[]
                for index,page in enumerate(doc):
                    if cancellation: cancellation.check()
                    body=page.get_text().strip()
                    if not body:
                        if cache_dir is None:
                            raise NeedsInput('PDF包含无文字页面，请通过新版任务入口自动OCR：'+str(path))
                        from .ocr import recognize
                        cache=Path(cache_dir)/f'page-{index+1:04d}'
                        cache.mkdir(parents=True,exist_ok=True)
                        image=cache/'page.png'
                        page.get_pixmap(matrix=pymupdf.Matrix(2,2)).save(str(image))
                        body=recognize(image,cache,cancellation)
                    pages.append(body)
                text='\n\n'.join(f'【第{i+1}页】\n{body}' for i,body in enumerate(pages))
        else:
            text = raw.decode(encoding)
    except ImportError as exc:
        raise NeedsInput('缺少文档组件：请安装python-docx和pymupdf。') from exc
    except (UnicodeError, LookupError) as exc:
        raise NeedsInput('无法按指定编码读取文件，请明确指定编码：' + str(path)) from exc
    if not text.strip() or '\x00' in text:
        raise NeedsInput('文件为空或包含不支持的二进制内容：' + str(path))
    return path, raw, text


def chunks(text, size):
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind('\n', start + size // 2, end)
            if boundary >= 0:
                end = boundary + 1
        yield start, end
        start = end
