import json
from pathlib import Path
import threading
import time
import uuid

from .artifacts import publish
from .context import summary_request
from .errors import Cancelled, NeedsInput, RetryableError, AwaitApproval
from .models import Cancellation, ModelConfig
from .store import TaskStore, atomic_bytes, atomic_json, file_hash, fingerprint, now
from .validators import validate_summary


class Engine:
    def __init__(self, store, client, retry_delay=1.0):
        self.store = store
        self.client = client
        self.retry_delay = retry_delay

    def _sources(self, task):
        sources = self.store.read_json('manifests/sources.json')
        if fingerprint(sources) != task['sources_hash']:
            raise NeedsInput('输入清单已变化，请重新建立任务。')
        for source in sources:
            try:
                moved=False
                if task.get('mode')=='move' and not Path(source['original']).exists():
                    try:
                        rec=self.store.read_json('manifests/moved.json').get(source['original'],{})
                        moved=bool(rec and file_hash(rec['destination'])==source['original_sha256'])
                    except (OSError,ValueError):
                        pass
                if not moved and file_hash(source['original']) != source['original_sha256']:
                    raise NeedsInput('输入文件已变化，请新建任务重新规划：' + source['original'])
                if file_hash(self.store.path(source['snapshot'])) != source['snapshot_sha256']:
                    raise NeedsInput('输入缓存损坏：' + source['snapshot'])
            except OSError as exc:
                raise NeedsInput('输入文件不可读取：' + source['original']) from exc
        return sources

    def run(self, cancellation=None, max_steps=None, on_event=None):
        token = cancellation or Cancellation()
        if max_steps is not None and max_steps < 1:
            raise ValueError('max_steps 必须为正数。')
        store = self.store
        # Shared task parent is the serial scheduler boundary.
        with store.lock(), TaskStore(store.root.parent).lock():
            run_id = uuid.uuid4().hex
            state = {'status':'running','run_id':run_id,'steps':{},'updated_at':now(),'error':''}
            stop_monitor = threading.Event()

            def emit(event, **fields):
                store.event(event, run_id=run_id, **fields)
                if on_event:
                    try:
                        on_event({'event':event,'run_id':run_id,**fields})
                    except Exception as exc:
                        store.event('ui_callback_error', error=str(exc)[:500])

            def save_state(status=None):
                if status:
                    state['status'] = status
                state['updated_at'] = now()
                store.write_json('state.json', state)

            def checkpoint(step, record):
                from .checkpoints import save_checkpoint
                state['checkpoint'] = save_checkpoint(store, plan, step, commits)
                if step.get('kind') == 'web_export':
                    # Project only this chapter, merging the existing manifest.
                    partial = publish(store, {'steps':[step]}, commits, sources)
                    from .workflow_runtime import finalize
                    finalize(store, task, {'steps':[step]}, commits, partial)
                    state['outputs'] = list(partial)
                save_state()

            def monitor():
                while not stop_monitor.wait(0.1):
                    try:
                        control = store.read_json('control.json')
                        if control.get('run_id') == run_id and control.get('action') == 'pause':
                            token.cancel()
                            return
                    except (OSError, ValueError):
                        continue

            watcher = threading.Thread(target=monitor, daemon=True)
            save_state()
            watcher.start()
            current = None
            try:
                task = store.task()
                plan = store.plan()
                from .scheduling import fiction_order
                scheduled=fiction_order(plan)
                if scheduled!=plan:
                    store.update_plan(scheduled)
                    task=store.task();plan=scheduled
                    emit('plan_rescheduled',reason='draft_before_auxiliary_summaries')
                config = ModelConfig(**task['config'])
                sources = self._sources(task)
                source_by_id = {s['id']:s for s in sources}
                commits = {}
                state['steps'] = {step['id']:'pending' for step in plan['steps']}
                emit('run_started', model=config.model, num_ctx=config.num_ctx, steps=len(plan['steps']))
                completed_this_run = 0
                for step in plan['steps']:
                    token.check()
                    current = step['id']
                    if step.get('kind')=='web_export':
                        from .workflow_runtime import name_web_export
                        name_web_export(store, task, step, commits, plan)
                    dependency_hashes = {sid:commits[sid]['artifact']['sha256'] for sid in dict.fromkeys(step['dependencies']+step.get('length_dependencies',[]))}
                    input_hash = fingerprint({'step':step,'dependencies':dependency_hashes,
                                              'source':source_by_id[step['document']]['snapshot_sha256'] if step.get('document') in source_by_id else task['sources_hash'],
                                              'config':task['config'],'instruction':task['instruction'],
                                              'options':task.get('options',{}),
                                              'workflow_version':plan['workflow_version'],'prompt_version':1})
                    record = store.committed(step, input_hash)
                    if record:
                        commits[current] = record
                        state['steps'][current] = 'succeeded'
                        checkpoint(step, record)
                        emit('step_reused', step_id=current)
                        if step.get('catalog'):
                            from .workflow_runtime import expand_catalog
                            expand_catalog(store,plan,step,record)
                            for node in plan['steps']:
                                state['steps'].setdefault(node['id'],'pending')
                        continue
                    state['current_step'] = current
                    state['steps'][current] = 'running'
                    save_state()
                    store.write_json(f'steps/{current}/input.json', {'input_hash':input_hash, 'step':step,
                                                                  'dependencies':dependency_hashes,'source':source_by_id.get(step.get('document'))})
                    previous_error = ''
                    for attempt_index in range(3):
                        token.check()
                        attempt = store.new_attempt(current)
                        started = time.monotonic()
                        emit('step_started', step_id=current, attempt=attempt.name, kind=step['kind'])
                        try:
                            if step['kind'] == 'summarize':
                                source = source_by_id[step['document']]
                                # Byte offsets preserve CRLF and avoid rereading a large source for every chunk.
                                with store.path(source['snapshot']).open('rb') as stream:
                                    stream.seek(step['byte_start'])
                                    text = stream.read(step['byte_end']-step['byte_start']).decode('utf-8')
                                request = summary_request(text, task['instruction'], config, previous_error)
                                atomic_json(attempt/'request.json', request)
                                result = self.client.generate(request, attempt, token)
                                if not (attempt/'response.txt').exists():
                                    atomic_bytes(attempt/'response.txt', str(result or '').encode('utf-8'))
                                token.check()
                                state['steps'][current] = 'validating'
                                save_state()
                                body = validate_summary(result)
                            elif step['kind'] == 'assemble':
                                atomic_json(attempt/'request.json', {'kind':'assemble','dependencies':dependency_hashes})
                                sections = ['# 分段摘要：' + source_by_id[step['document']]['name'],
                                            '以下按原文顺序汇集各片段摘要，尚未进行全篇语义综合。']
                                for number, sid in enumerate(step['dependencies'], 1):
                                    token.check()
                                    sections.append(f'## 片段 {number}\n\n' + store.path(commits[sid]['artifact']['path']).read_text(encoding='utf-8'))
                                body = '\n\n'.join(sections)
                            else:
                                from .workflow_runtime import execute_step
                                body = execute_step(store, task, step, commits, config, self.client, token, attempt, previous_error, retry_index=attempt_index)
                            token.check()
                            artifact = attempt/'output.txt'
                            if isinstance(body, (bytes, bytearray)):
                                raw=bytes(body)
                                size=len(raw)
                            else:
                                body=str(body)
                                raw=(body.rstrip()+'\n').encode('utf-8')
                                size=len(body)
                            atomic_bytes(artifact, raw)
                            atomic_json(attempt/'validation.json', {'valid':True,'chars':size,'at':now()})
                            token.check()
                            record = store.commit(step, input_hash, artifact, run_id)
                            commits[current] = record
                            state['steps'][current] = 'succeeded'
                            checkpoint(step, record)
                            self._metadata(attempt, status='succeeded', elapsed=time.monotonic()-started)
                            save_state()
                            emit('step_succeeded', step_id=current, artifact=record['artifact'])
                            if step.get('catalog'):
                                from .workflow_runtime import expand_catalog
                                expand_catalog(store,plan,step,record)
                                for node in plan['steps']:
                                    state['steps'].setdefault(node['id'],'pending')
                            break
                        except (RetryableError, NeedsInput, Cancelled) as exc:
                            self._metadata(attempt, status='paused' if isinstance(exc,Cancelled) else 'failed',
                                           error=str(exc), error_type=type(exc).__name__, elapsed=time.monotonic()-started)
                            atomic_json(attempt/'validation.json', {'valid':False,'reason':str(exc),'at':now()})
                            emit('step_attempt_failed', step_id=current, attempt=attempt.name, error=str(exc), error_type=type(exc).__name__)
                            if not isinstance(exc, RetryableError) or attempt_index == 2:
                                raise
                            previous_error = str(exc)
                            state['steps'][current] = 'retry_wait'
                            save_state()
                            token.event.wait(self.retry_delay * (2 ** attempt_index))
                            token.check()
                    completed_this_run += 1
                    if max_steps and completed_this_run >= max_steps:
                        state['current_step'] = None
                        save_state('paused')
                        emit('run_paused', reason='step_limit')
                        return state
                token.check()
                self._sources(task)
                save_state('validating')
                artifacts = publish(store, plan, commits, sources)
                if task.get('workflow'):
                    from .workflow_runtime import finalize
                    artifacts = finalize(store, task, plan, commits, artifacts)
                token.check()
                state['outputs'] = list(artifacts)
                state['current_step'] = None
                save_state('completed')
                emit('run_completed', outputs=list(artifacts))
            except AwaitApproval as exc:
                try:
                    self._sources(task)
                    partial=publish(store,plan,commits,sources)
                    from .workflow_runtime import finalize
                    finalize(store,task,plan,commits,partial)
                    state['outputs']=list(partial)
                    state['approval'] = exc.approval
                    state['error'] = ''
                    state['steps'][current] = 'awaiting_approval'
                    save_state('awaiting_approval')
                    emit('awaiting_approval', approval=exc.approval)
                except Exception as failure:
                    state['error']=str(failure)
                    state['error_type']=type(failure).__name__
                    save_state('needs_input' if isinstance(failure,NeedsInput) else 'failed')
                    emit('run_failed',error=str(failure),error_type=type(failure).__name__)
            except Cancelled as exc:
                state['error'] = str(exc)
                if current and state['steps'].get(current) != 'succeeded':
                    state['steps'][current] = 'pending'
                save_state('paused')
                emit('run_paused', reason=str(exc))
            except Exception as exc:
                state['error'] = str(exc)
                state['error_type'] = type(exc).__name__
                if current and state['steps'].get(current) != 'succeeded':
                    state['steps'][current] = 'failed'
                save_state('needs_input' if isinstance(exc, NeedsInput) else 'failed')
                emit('run_failed', error=str(exc), error_type=type(exc).__name__)
            finally:
                stop_monitor.set()
                watcher.join(timeout=1)
            return state

    @staticmethod
    def _metadata(attempt, **fields):
        path = attempt/'metadata.json'
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            data = {}
        data.update(fields)
        atomic_json(path, data)
