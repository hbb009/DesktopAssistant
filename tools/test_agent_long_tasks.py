import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1] if Path(__file__).parent.name=='tools' else Path(__file__).parent))
from Agent.agent_core.catalog_range import chapter_range
from Agent.agent_core.engine import Engine
from Agent.agent_core.errors import RetryableError
from Agent.agent_core.models import ModelConfig
from Agent.agent_core.workflows.planner import create_task
from Agent.agent_core.conversation import validate_intent


class LongTasks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []
        self.fail = None
        self.fake = types.ModuleType('utils.ollama_auto')
        from utils.ollama_auto import PageError
        self.fake.PageError = PageError
        self.fake.pick_chapter_links = lambda links, url: links
        import re
        self.fake._NEXT_PAGE_RE = re.compile(r'^下一页$')
        self.fake._same_chapter = lambda a, b: False
        self.fake.fetch_page = self.fetch
        self.patcher = patch.dict(sys.modules, {'utils.ollama_auto': self.fake})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def fetch(self, url, **kwargs):
        self.calls.append(url)
        if url == self.fail:
            raise RetryableError('simulated timeout')
        if url.endswith('/catalog'):
            return {'url':url, 'title':'目录', 'text':'完整目录',
                    'links':[(f'第{i}章 测试',f'https://example.test/{i}') for i in range(1, 602)]}
        return {'url':url, 'title':'章节', 'text':'章节正文 '+url, 'links':[]}

    def task(self, text):
        return create_task(self.root/'tasks','web','catalog',[],text,ModelConfig('fake'),
                           {'urls':'https://example.test/catalog','export_root':str(self.root)})

    def test_range_and_resume(self):
        store = self.task('下载001章-300章，保存为文本文件')
        first = Engine(store,None,0).run(max_steps=7)
        self.assertEqual(first['status'],'paused',first)
        self.assertTrue(first['outputs'])
        self.assertTrue(list((self.root/'Agent成果').rglob('第1章*.txt')))
        count = len(self.calls)
        state = Engine(store,None,0).run(max_steps=2)
        self.assertEqual(state['status'],'paused',state)
        self.assertEqual(len(self.calls)-count,1)
        self.assertEqual(self.calls.count('https://example.test/1'),1)
        self.assertEqual(len(store.plan()['steps']),602)
        self.assertEqual(len(list(store.path('manifests/catalog').glob('*.json'))),12)
        self.assertTrue(store.path(first['checkpoint']).exists())

    def test_large_catalog_and_retry(self):
        store=self.task('下载所有章节为文本文件')
        self.fail='https://example.test/3'
        state=Engine(store,None,0).run()
        self.assertEqual(state['status'],'failed',state)
        self.assertEqual(len(store.plan()['steps']),1204)
        self.assertTrue(list((self.root/'Agent成果').rglob('第2章*.txt')))
        self.fail=None
        state=Engine(store,None,0).run(max_steps=2)
        self.assertEqual(state['status'],'paused',state)
        self.assertEqual(self.calls.count('https://example.test/2'),1)
        self.assertTrue(list((self.root/'Agent成果').rglob('第3章*.txt')))

    def test_finish_and_segment_files(self):
        store=self.task('下载第10章至第40章为文本文件')
        state=Engine(store,None,0).run()
        self.assertEqual(state['status'],'completed',state)
        self.assertEqual(len(state['outputs']),32)  # catalog + 31 chapters
        self.assertEqual(len(list(store.path('checkpoints').glob('*.json'))),3)
        self.assertNotIn('https://example.test/9',self.calls)
        self.assertNotIn('https://example.test/41',self.calls)
        before=len(self.calls)
        state=Engine(store,None,0).run()
        self.assertEqual(state['status'],'completed',state)
        self.assertEqual(len(self.calls),before)

    def test_modified_output_preserved(self):
        store=self.task('下载第1章至第2章为文本文件')
        Engine(store,None,0).run(max_steps=4)
        output=next((self.root/'Agent成果').rglob('第1章*.txt'))
        output.write_text('user edit',encoding='utf-8')
        state=Engine(store,None,0).run()
        self.assertEqual(state['status'],'needs_input',state)
        self.assertEqual(output.read_text(encoding='utf-8'),'user edit')

    def test_parser(self):
        self.assertEqual(chapter_range('001章-300章节命名保存为文本文件'),(1,300))
        self.assertEqual(chapter_range('第81章至第100章'),(81,100))
        self.assertIsNone(chapter_range('全部章节'))
        intent=validate_intent({'action':'task','workflow':'web','mode':'catalog',
                               'urls':['https://example.test/catalog'], 'options':{'chapters':601}},
                              {'root':str(self.root),'files':{},'skills':{}})
        self.assertNotIn('chapters',intent['options'])

    def test_document_chunk_resume(self):
        from Agent.agent_core.workflows.documents import create_document_task
        class Client:
            calls=0
            def generate(self,request,attempt,token):
                self.calls+=1
                return '项目记录摘要：需求已经整理完成，材料按顺序核对，处理结果保存在本地供后续检查。'
        source=self.root/'source.txt'
        source.write_text('项目执行记录。整理需求，核对数据，记录结果，完成后保存。\n'*500,encoding='utf-8')
        client=Client()
        store=create_document_task(self.root/'tasks',[source],'摘要',ModelConfig('fake',chunk_chars=400))
        state=Engine(store,client,0).run(max_steps=26)
        self.assertEqual(state['status'],'paused',state)
        self.assertEqual(len(list(store.path('checkpoints').glob('*.json'))),2)
        old_calls=client.calls
        state=Engine(store,client,0).run(max_steps=1)
        self.assertEqual(state['status'],'paused',state)
        self.assertEqual(client.calls,old_calls+1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
