import hashlib
from pathlib import Path

from ..adapters.documents import chunks, read_document
from ..budget import check_budget
from ..context import summary_request
from ..store import TaskStore, atomic_bytes, fingerprint, now


def create_document_task(parent, sources, instruction, config, encoding='utf-8-sig'):
    """Freeze explicit source files; never recursively ingest generated outputs."""
    if not sources or not instruction.strip():
        raise ValueError('需要至少一个输入文件和完整任务要求。')
    paths, seen = [], set()
    for source in sources:
        path, raw, text = read_document(source, encoding)
        if path in seen:
            continue
        seen.add(path)
        # Fail oversized instructions before creating a half-built task.
        for start, end in chunks(text, config.chunk_chars):
            summary_request(text[start:end], instruction, config)
        paths.append(path)
    store = TaskStore.create(parent)
    manifest, steps = [], []
    for index, source in enumerate(paths, 1):
        # Keep at most one document in memory, never all 80 source chapters.
        path, raw, text = read_document(source, encoding)
        doc_id = f'doc-{index:04d}'
        snapshot = f'inputs/{doc_id}.txt'
        atomic_bytes(store.path(snapshot), text.encode('utf-8'))
        manifest.append({'id':doc_id, 'original':str(path), 'original_sha256':hashlib.sha256(raw).hexdigest(),
                         'snapshot':snapshot, 'snapshot_sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
                         'chars':len(text), 'encoding':encoding, 'name':path.name})
        dependencies = []
        byte_start = 0
        for number, (start,end) in enumerate(chunks(text,config.chunk_chars), 1):
            summary_request(text[start:end], instruction, config)
            byte_end = byte_start + len(text[start:end].encode('utf-8'))
            sid = f'{doc_id}-chunk-{number:05d}'
            steps.append({'id':sid,'kind':'summarize','document':doc_id,'start':start,'end':end,
                          'byte_start':byte_start,'byte_end':byte_end,'dependencies':[]})
            dependencies.append(sid)
            byte_start = byte_end
        steps.append({'id':f'{doc_id}-assemble','kind':'assemble','document':doc_id,'dependencies':dependencies})
    plan = {'workflow':'document_summary','workflow_version':1,'steps':steps}
    store.write_json('manifests/sources.json', manifest)
    store.write_json('plan.json', plan)
    store.write_json('task.json', {'schema_version':1,'task_id':store.root.name,'created_at':now(),
                                 'instruction':instruction,'config':config.to_dict(),'plan_hash':fingerprint(plan),
                                 'sources_hash':fingerprint(manifest)})
    store.write_json('state.json', {'status':'ready','steps':{s['id']:'pending' for s in steps},'run_id':None})
    store.event('task_created', documents=len(manifest), steps=len(steps))
    return store
