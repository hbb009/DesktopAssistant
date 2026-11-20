from pathlib import Path

from .errors import NeedsInput
from .store import atomic_bytes, file_hash


def publish(store, plan, commits, sources):
    """Idempotently project immutable committed artifacts into task outputs."""
    try:
        previous = store.read_json('manifests/artifacts.json')
    except (OSError, ValueError):
        previous = {}
    published = dict(previous)
    by_id = {source['id']:source for source in sources}
    for step in plan['steps']:
        if step['id'] not in commits:
            continue
        if step['kind'] != 'assemble' and not step.get('publish'):
            continue
        artifact = commits[step['id']]['artifact']
        # Numeric source identity avoids basename collisions and path injection.
        rel = 'outputs/' + step['publish'] if step.get('publish') else f"outputs/{step['document']}-summary.md"
        dest = store.path(rel)
        if dest.exists():
            existing = file_hash(dest)
            old = previous.get(rel, {}).get('sha256')
            if existing != artifact['sha256'] and existing != old:
                raise NeedsInput('输出文件已被修改，未覆盖，请保留或另存后处理：' + str(dest))
        if not dest.exists() or file_hash(dest) != artifact['sha256']:
            atomic_bytes(dest, store.path(artifact['path']).read_bytes())
        if file_hash(dest) != artifact['sha256']:
            raise NeedsInput('输出校验失败：' + str(dest))
        published[rel] = {'sha256':artifact['sha256'],'step_id':step['id'],
                          'source':by_id.get(step.get('document'), {}).get('original',''),'artifact':artifact['path']}
        # Persist after every file so a crash between documents is recoverable.
        store.write_json('manifests/artifacts.json', published)
    return published
