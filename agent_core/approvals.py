"""Only explicit UI/user actions create approval records."""
from .errors import NeedsInput
from .store import TaskStore, file_hash, fingerprint, now


def approval_key(chapter, draft_hash):
    return f'approvals/chapter-{chapter:04d}-{draft_hash}.json'


def confirm_chapter(store, expected_hash):
    with store.lock(), TaskStore(store.root.parent).lock():
        state = store.read_json('state.json')
        approval = state.get('approval') or {}
        if state.get('status') != 'awaiting_approval' or expected_hash != approval.get('draft_hash'):
            raise NeedsInput('待确认章节或稿件版本已经变化，请重新打开当前稿件。')
        if file_hash(store.path(approval['draft_path'])) != expected_hash:
            raise NeedsInput('稿件内容已变化，本次确认无效。')
        for source in store.read_json('manifests/sources.json'):
            if file_hash(source['original'])!=source['original_sha256']:
                raise NeedsInput('输入文件已变化，请重新建立任务后确认。')
        record = {**approval, 'approved_at':now(), 'actor':'user'}
        store.write_json(approval_key(approval['chapter'],expected_hash), record)
        # Formal chapter pointer commits only after the user's approval.
        store.write_json(f"memory/story/chapter-{approval['chapter']:04d}.json",record)
        state.update(status='ready', approval=None, error='')
        store.write_json('state.json',state)
        store.event('chapter_approved',chapter=approval['chapter'],draft_hash=expected_hash)


def revise_chapter(store, feedback, expected_hash):
    if not feedback.strip():
        raise NeedsInput('请填写本章修改或重写要求。')
    with store.lock(), TaskStore(store.root.parent).lock():
        state=store.read_json('state.json')
        approval=state.get('approval') or {}
        if state.get('status')!='awaiting_approval' or approval.get('draft_hash')!=expected_hash:
            raise NeedsInput('只能修改当前等待确认的章稿。')
        task=store.task()
        plan=store.plan()
        for step in plan['steps']:
            if step.get('chapter')==approval['chapter'] and step['kind']=='generate':
                step['revision']=int(step.get('revision',0))+1
                step['feedback']=feedback.strip()
        digest=fingerprint(plan)
        relative=f'plans/{digest}.json'
        store.write_json(relative,plan)
        task.update(plan_path=relative,plan_hash=digest)
        store.write_json('task.json',task)
        state.update(status='ready',approval=None,error='')
        store.write_json('state.json',state)
        store.event('chapter_revision_requested',chapter=approval['chapter'],previous_hash=expected_hash,feedback=feedback)
