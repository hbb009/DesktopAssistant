from pathlib import Path
from .errors import NeedsInput
from .store import TaskStore, file_hash, fingerprint, atomic_bytes, now


def move_plan(store):
    task=store.task()
    root=task.get('options',{}).get('export_root')
    if not root:
        raise NeedsInput('移动文件需要指定工作目录。')
    from .workflow_runtime import safe_name
    base=(Path(root).resolve()/'Agent成果'/task['task_id']/'文件').resolve()
    return [{'source':s['original'],'sha256':s['original_sha256'],
             'destination':str(base/f'{i:03d}-{safe_name(s["name"])}')}
            for i,s in enumerate(store.read_json('manifests/sources.json'),1)]


def confirm_move(store,expected):
    with store.lock(),TaskStore(store.root.parent).lock():
        plan=move_plan(store)
        if fingerprint(plan)!=expected:
            raise NeedsInput('移动清单已变化，请重新确认。')
        store.write_json('approvals/move.json',{'plan_hash':expected,'actor':'user','at':now()})


def finish_move(store):
    plan=move_plan(store)
    try:
        approval=store.read_json('approvals/move.json')
    except (OSError,ValueError):
        raise NeedsInput('需要确认具体移动清单，尚未移除源文件。')
    if approval.get('plan_hash')!=fingerprint(plan) or approval.get('actor')!='user':
        raise NeedsInput('移动清单的确认已失效。')
    try:
        moved=store.read_json('manifests/moved.json')
    except (OSError,ValueError):
        moved={}
    for item in plan:
        src,dest=Path(item['source']),Path(item['destination'])
        if file_hash(dest)!=item['sha256']:
            raise NeedsInput('移动目标校验失败，未删除源文件：'+str(dest))
        if src.exists():
            if file_hash(src)!=item['sha256']:
                raise NeedsInput('源文件已变化，未删除：'+str(src))
            # Write intent before unlink. Recovery can validate the completed copy.
            moved[str(src)]={**item,'status':'copy_verified'}
            store.write_json('manifests/moved.json',moved)
            src.unlink()
        moved[str(src)]={**item,'status':'moved'}
        store.write_json('manifests/moved.json',moved)
