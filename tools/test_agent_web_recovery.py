"""Offline regression coverage for real HTTP errors, pagination and recovery."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
from utils import ollama_auto
from Agent.agent_core.engine import Engine
from Agent.agent_core.models import ModelConfig, Cancellation
from Agent.agent_core.workflows.planner import create_task

URL = 'https://example.test/book/1/chapter.html'
NEXT = 'https://example.test/book/1/chapter_2.html'


def html(label='正文', next_page=False):
    nav = '<a href="chapter_2.html">下一页</a>' if next_page else ''
    return '<html><h1>测试章</h1><div id="content">' + label * 80 + '</div>' + nav + '</html>'


class WebRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def task(self):
        return create_task(self.root/'tasks', 'web', 'page', [], '保存为文本文件',
                           ModelConfig('fake'), {'urls':URL, 'export_root':str(self.root)})

    def events(self, store):
        return [json.loads(line) for line in store.path('logs/events.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_three_observed_network_errors_retry_and_complete(self):
        for error in (requests.ConnectionError('Connection reset 10054'),
                      requests.ReadTimeout('read timeout=15'),
                      requests.exceptions.ProxyError('Remote disconnected')):
            with self.subTest(error=type(error).__name__):
                store = self.task()
                with patch.object(ollama_auto, '_get_html', side_effect=[error, (html(), URL)]) as fetch:
                    state = Engine(store, None, 0).run()
                self.assertEqual(state['status'], 'completed', state)
                self.assertEqual(fetch.call_count, 2)
                failures = [e for e in self.events(store) if e['event']=='step_attempt_failed']
                self.assertEqual(len(failures), 1)
                self.assertEqual(failures[0]['error_type'], 'RetryableError')

    def test_http_retryable_and_permanent_statuses(self):
        for status in (408, 429, 500, 503, 404, 403):
            with self.subTest(status=status):
                response=requests.Response(); response.status_code=status
                error=requests.HTTPError(str(status), response=response)
                store=self.task()
                with patch.object(ollama_auto, '_get_html', side_effect=error) as fetch:
                    state=Engine(store,None,0).run()
                retryable=status in (408,429,500,503)
                self.assertEqual(fetch.call_count,3 if retryable else 1)
                self.assertEqual(state['status'],'failed' if retryable else 'needs_input')

    def test_pagination_retry_resume_reuses_cached_first_page(self):
        store=self.task(); calls=[]; offline=True
        def fetch(url):
            calls.append(url)
            if url==URL: return html('第一页',True),url
            self.assertEqual(url,NEXT)
            if offline: raise requests.ReadTimeout('second page offline')
            return html('第二页'),url
        with patch.object(ollama_auto,'_get_html',side_effect=fetch):
            state=Engine(store,None,0).run()
            self.assertEqual(state['status'],'failed',state)
            self.assertEqual(calls.count(URL),1)
            self.assertEqual(calls.count(NEXT),3)
            self.assertFalse(list(store.path('outputs').rglob('*.txt')))
            offline=False
            state=Engine(store,None,0).run()
            self.assertEqual(state['status'],'completed',state)
            self.assertEqual(calls.count(URL),1)
            saved=next(store.path('outputs').rglob('*.txt')).read_text(encoding='utf-8')
            self.assertIn('第一页'*80,saved)
            self.assertIn('第二页'*80,saved)
            before=len(calls)
            self.assertEqual(Engine(store,None,0).run()['status'],'completed')
            self.assertEqual(len(calls),before)

    def test_pause_during_failed_request_does_not_retry(self):
        store=self.task(); token=Cancellation()
        def fetch(url):
            token.cancel()
            raise requests.ConnectionError('interrupted')
        with patch.object(ollama_auto,'_get_html',side_effect=fetch) as request:
            state=Engine(store,None,0).run(token)
        self.assertEqual(state['status'],'paused',state)
        self.assertEqual(request.call_count,1)

    def test_pause_during_retry_wait(self):
        store=self.task(); token=Cancellation()
        def event(e):
            if e['event']=='step_attempt_failed': token.cancel()
        with patch.object(ollama_auto,'_get_html',side_effect=requests.ReadTimeout()) as request:
            state=Engine(store,None,30).run(token,on_event=event)
        self.assertEqual(state['status'],'paused',state)
        self.assertEqual(request.call_count,1)

    def test_non_html_does_not_retry(self):
        store=self.task()
        with patch.object(ollama_auto,'_get_html',side_effect=ollama_auto.PageError('不是网页')) as fetch:
            state=Engine(store,None,0).run()
        self.assertEqual(state['status'],'needs_input',state)
        self.assertEqual(fetch.call_count,1)

    def test_legacy_fetch_does_not_silently_return_partial_chapter(self):
        with patch.object(ollama_auto,'_get_html',side_effect=[(html('第一页',True),URL),requests.ReadTimeout()]):
            with self.assertRaises(ollama_auto.PageError) as caught:
                ollama_auto.fetch_page(URL)
        self.assertTrue(caught.exception.retryable)


if __name__=='__main__':
    unittest.main(verbosity=2)
