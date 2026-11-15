"""Agent regression tests, no model service or user data required."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils import ollama_chat as chat
from utils.ollama_auto import choose_ctx, ceiling
from utils.ollama_agent import run_fncall, _context_stopped, _compact_old_tools
from utils.ollama_progress import ProgressSession, extract_task_card
from utils.ollama_intent import classify


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'chapter.txt'
        self.source.write_text('source material', encoding='utf-8')
        self.p = ProgressSession(str(self.root), store_dir=str(self.root / 'state'))

    def test_loaded_window_can_grow(self):
        self.assertEqual(choose_ctx(5000, 262144, 16384), 16384)
        self.assertGreater(choose_ctx(18000, 262144, 16384), 16384)
        self.assertEqual(ceiling(32768, 16384), 32768)

    def test_auto_budget_counts_tool_arguments(self):
        with patch.object(chat, '_CTX', type('Ctx', (), {'loaded': 16384, 'max_ctx': 65536})()):
            n = chat._auto_ctx([{'role': 'assistant', 'tool_calls': [{'function': {'arguments': {'content': '字' * 18000}}}]}], [], False)
        self.assertGreater(n, 16384)

    def test_notes_tool_available_in_both_modes(self):
        for obedient in (True, False):
            self.assertIn('save_notes', classify('读取全部章节并制作Skill', dirs=True, obedient=obedient).tools)

    def test_read_and_excerpt_are_not_notes(self):
        self.p.mark_read('chapter.txt', got=50, complete=True)
        self.p._store_excerpt('chapter.txt', 'excerpt ' * 100)
        self.assertFalse(self.p.has_notes_for('chapter.txt'))
        self.assertFalse(self.p.can_compact_files(['chapter.txt']))

    def test_notes_cover_only_named_files_and_current_offset(self):
        self.p.mark_read('chapter.txt', got=50)
        self.p.mark_read('other.txt', got=50)
        self.p.save_notes('Analysis facts and decisions. ' * 20, files=['chapter.txt'])
        self.assertTrue(self.p.has_notes_for('chapter.txt'))
        self.assertFalse(self.p.has_notes_for('other.txt'))
        self.p.mark_read('chapter.txt', start=50, got=50)
        self.assertFalse(self.p.has_notes_for('chapter.txt'))
        self.p.save_notes('Analysis facts and decisions. ' * 20, files=['chapter.txt'])
        self.assertFalse(self.p.has_notes_for('chapter.txt'))

    def test_unnoted_material_not_compacted(self):
        text = '【本次读取的文件：chapter.txt】\n' + 'original text ' * 100
        thread = [{'role': 'tool', 'content': text}, {'role': 'tool', 'content': 'new result'}]
        self.p.mark_read('chapter.txt', got=50)
        _compact_old_tools(thread, aggressive=True, progress=self.p)
        self.assertEqual(thread[0]['content'], text)

    def test_all_sources_required_and_partial_output_incomplete(self):
        self.p.data['task_card'] = extract_task_card('读取全部章节并保存为 result.md', ['chapter.txt'])
        self.p.mark_written('result.md', chars=100)
        self.assertFalse(self.p.success_met())
        self.p.mark_read('chapter.txt', got=len('source material'), complete=True)
        self.assertTrue(self.p.success_met())
        self.p.data['written']['result.md']['complete'] = False
        self.assertFalse(self.p.success_met())
        self.assertIn('result.md', self.p.missing_outputs())

    def test_long_task_preserved(self):
        request = '分析材料。' * 500 + '保存为 result.md'
        card = extract_task_card(request, ['chapter.txt'])
        self.assertEqual(card['user_input'], request)
        self.assertIn('result.md', card['outputs'])

    def test_http500_is_not_context_overflow(self):
        self.assertFalse(_context_stopped(RuntimeError('HTTP 500 unknown error')))
        self.assertTrue(_context_stopped(RuntimeError('HTTP 500 context length exceeded')))

    def run_rounds(self, steps, review=None):
        executed = []
        def round_iter(messages, tools, sink, think):
            step = steps.pop(0)
            if isinstance(step, Exception):
                raise step
            sink.update(content=[step], calls=[], done_reason='stop')
            yield step
        result = ''.join(run_fncall(round_iter=round_iter, exec_tool=lambda *a: executed.append(a), messages=[{'role':'user','content':'task'}], tools=[], stop_review=review, max_calls=12))
        return result

    def test_repeated_premature_finish_reported_incomplete(self):
        steps = ['I will save next.'] * 4
        out = self.run_rounds(steps, lambda spoken: 'result.md still missing')
        self.assertFalse(steps)
        self.assertIn('未完成', out)

    def test_server_error_is_bounded_and_reason_visible(self):
        steps = [RuntimeError('HTTP 500 backend crashed')] * 4
        out = self.run_rounds(steps)
        self.assertFalse(steps)
        self.assertIn('backend crashed', out)
        self.assertIn('未完成', out)

    def test_successful_round_resets_server_errors(self):
        steps = [RuntimeError('HTTP 500'), RuntimeError('HTTP 500'), 'not done', RuntimeError('HTTP 500'), RuntimeError('HTTP 500'), 'finished']
        out = self.run_rounds(steps, lambda s: '' if s == 'finished' else 'keep working')
        self.assertFalse(steps)
        self.assertIn('finished', out)

    def test_context_retries_reset_after_success(self):
        steps = [RuntimeError('context length exceeded')] * 2 + ['not done'] + [RuntimeError('context length exceeded')] * 2 + ['finished']
        def iterator(messages, tools, sink, think):
            step = steps.pop(0)
            if isinstance(step, Exception):
                raise step
            sink.update(content=[step], calls=[], done_reason='stop')
            yield step
        out = ''.join(run_fncall(round_iter=iterator, exec_tool=lambda *a: '', messages=[{'role':'user','content':'task'}], tools=[], progress=self.p, stop_review=lambda s: '' if s == 'finished' else 'keep working', max_calls=12))
        self.assertFalse(steps)
        self.assertIn('finished', out)

    def test_saved_note_arguments_compact_without_losing_durable_notes(self):
        body = 'Unique facts for the chapter. ' * 140
        self.p.mark_read('chapter.txt', got=50)
        self.p.save_notes(body, files=['chapter.txt'])
        call = {'function': {'name': 'save_notes', 'arguments': {'content': body}}}
        thread = [{'role':'assistant', 'tool_calls':[call]}, {'role':'tool','name':'save_notes','content':'已记下笔记：chapter.txt'}, {'role':'user','content':'next'}]
        _compact_old_tools(thread, aggressive=True, progress=self.p)
        self.assertLess(len(thread[0]['tool_calls'][0]['function']['arguments']['content']), 200)
        self.assertEqual(call['function']['arguments']['content'], body)
        self.assertIn('Unique facts', self.p.read_notes())
        self.assertIn('read_notes(start=3000)', self.p.read_notes())
        self.assertIn('笔记已读完', self.p.read_notes(3000))

    def test_real_tools_read_notes_and_segmented_save(self):
        from utils.ollama_dirs import run_dir_tool as run_tool
        from utils.ollama_chat import _run_tool
        result = run_tool('read_article', {'filename':'chapter.txt'}, article_dir=str(self.root), progress=self.p, read_chars=1000)
        self.assertIn('source material', result)
        self.assertFalse(self.p.has_notes_for('chapter.txt'))
        result = run_tool('save_notes', {'files':'chapter.txt', 'content':'Analysis facts and future steps. ' * 10}, article_dir=str(self.root), progress=self.p)
        self.assertIn('已记下笔记', result)
        self.assertTrue(self.p.has_notes_for('chapter.txt'))
        result = run_tool('read_notes', {}, article_dir=str(self.root), progress=self.p)
        self.assertIn('Analysis facts', result)
        statuses = []
        result = _run_tool('read_notes', {}, statuses.append, dirs=True, article_dir=str(self.root), progress=self.p)
        self.assertIn('Analysis facts', result)
        self.assertTrue(statuses)
        self.p.data['task_card'] = {'outputs':['result.md']}
        first = 'First section. ' * 20
        second = 'Second section. ' * 20
        result = run_tool('save_article', {'filename':'result.md','content':first,'complete':False}, article_dir=str(self.root), progress=self.p)
        self.assertIn('已保存', result)
        self.assertFalse(self.p.success_met())
        result = run_tool('save_article', {'filename':'result.md','content':second,'mode':'append','complete':True}, article_dir=str(self.root), progress=self.p)
        self.assertIn('已保存', result)
        self.assertTrue(self.p.success_met())
        saved = (self.root/'result.md').read_text(encoding='utf-8')
        self.assertIn(first.strip(), saved)
        self.assertIn(second.strip(), saved)

    def test_save_schema_requires_content(self):
        tool = chat._tools_named(['save_article'])[0]
        self.assertIn('content', tool['function']['parameters']['required'])

    def test_empty_save_retries_with_focused_schema_then_restores_tools(self):
        from utils.ollama_dirs import run_dir_tool
        target = 'package/evaluation.md'
        body = '# Evaluation\n\nVerify clear structure and accurate references.\n'
        toolsets = []
        def iterator(messages, tools, sink, think):
            toolsets.append(tools)
            n = len(toolsets)
            if n == 1:
                args = {'filename':target}
            elif n == 2:
                self.assertEqual([t['function']['name'] for t in tools], ['save_article'])
                params = tools[0]['function']['parameters']
                self.assertEqual(params['properties']['filename']['enum'], [target])
                self.assertEqual(params['properties']['content']['minLength'], 1)
                args = {'filename':target, 'content':body}
            else:
                self.assertIn('read_article', [t['function']['name'] for t in tools])
                sink.update(content=['done'], calls=[], done_reason='stop')
                yield 'done'
                return
            sink.update(content=[], calls=[{'type':'function','function':{'name':'save_article','arguments':args}}], done_reason='stop')
            return
            yield ''
        def execute(name, args, spoken=''):
            return run_dir_tool(name, args, article_dir=str(self.root), reply_body=spoken)
        out = ''.join(run_fncall(round_iter=iterator, exec_tool=execute, messages=[{'role':'user','content':'Create evaluation file'}], tools=chat._tools_named(['read_article','save_article']), max_calls=5))
        self.assertEqual(len(toolsets), 3)
        self.assertEqual((self.root/target).read_text(encoding='utf-8').strip(), body.strip())
        self.assertIn('done', out)


if __name__ == '__main__':
    unittest.main()
