"""Reuse existing extraction, persist each page, and fail visibly on missing pages."""
import hashlib
from urllib.parse import urlsplit

from ..errors import NeedsInput, RetryableError


def fetch_cached(store,url,token):
    from utils.ollama_auto import fetch_page, PageError, _NEXT_PAGE_RE, _same_chapter
    parts=[]
    links=[]
    title=''
    seen=set()
    current=url
    while current:
        token.check()
        if current in seen:
            raise NeedsInput('分页链接循环，未将不完整网页记成完成：'+current)
        if len(seen)>=30:
            raise NeedsInput('同一网页超过30页，请拆分网址任务。')
        seen.add(current)
        key=hashlib.sha256(current.encode()).hexdigest()
        cache=f'inputs/web/{key}.json'
        try:
            page=store.read_json(cache)
        except (OSError,ValueError):
            try:
                page=fetch_page(current,should_stop=token.event.is_set,max_pages=1,link_limit=0)
            except PageError as exc:
                token.check()
                if exc.retryable:
                    raise RetryableError(str(exc)) from exc
                raise NeedsInput(str(exc)) from exc
            token.check()
            store.write_json(cache,page)
        if not page.get('text','').strip():
            raise NeedsInput('网页没有可保存的正文：'+current)
        title=title or page.get('title','网页正文')
        parts.append(page['text'])
        links.extend(page.get('links') or [])
        # Pagination is intentionally absent from the general navigation links.
        next_page=page.get('next_page')
        if next_page and not _same_chapter(current,next_page):
            raise NeedsInput('分页链接不属于当前章节：'+next_page)
        current=next_page or next((href for name,href in page.get('links',[]) if _NEXT_PAGE_RE.match(name.strip()) and _same_chapter(current,href)),None)
    return {'url':url,'title':title,'text':'\n\n'.join(parts),'links':links,'pages':list(seen)}
