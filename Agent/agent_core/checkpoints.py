"""Small durable journals referencing individually committed step artifacts."""
from .store import now

BATCH_SIZE = 25


def save_checkpoint(store, plan, step, commits):
    index = next(i for i, node in enumerate(plan['steps']) if node['id'] == step['id'])
    batch = index // BATCH_SIZE
    nodes = plan['steps'][batch * BATCH_SIZE:(batch + 1) * BATCH_SIZE]
    records = {node['id']: commits[node['id']] for node in nodes if node['id'] in commits}
    relative = f'checkpoints/segment-{batch + 1:05d}.json'
    store.write_json(relative, {'schema_version': 1, 'updated_at': now(),
        'segment': batch + 1, 'complete': len(records) == len(nodes),
        'steps': records})
    return relative
