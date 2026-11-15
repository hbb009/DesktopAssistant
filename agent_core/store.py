from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

from .errors import BusyError, NeedsInput


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers (including UI polling and antivirus) can briefly deny
        # replace while their handle is open. Retry the atomic rename, not a
        # non-atomic overwrite, and retain the previous valid state throughout.
        for retry in range(10):
            try:
                os.replace(name, path)
                break
            except PermissionError:
                if retry==9:
                    raise
                time.sleep(min(.01*(2**retry),.2))
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_json(path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


class TaskStore:
    SCHEMA = 1

    def __init__(self, root):
        self.root = Path(root).resolve()

    @classmethod
    def create(cls, parent):
        parent = Path(parent).resolve()
        parent.mkdir(parents=True, exist_ok=True)
        root = parent / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:10])
        root.mkdir()
        store = cls(root)
        for folder in ('manifests', 'inputs', 'steps', 'outputs', 'logs'):
            store.path(folder).mkdir()
        return store

    def path(self, relative):
        rel = Path(relative)
        if rel.is_absolute() or not rel.parts:
            raise ValueError('需要任务目录内的相对路径。')
        dest = (self.root / rel).resolve()
        if dest == self.root or not dest.is_relative_to(self.root):
            raise ValueError('路径不能越出任务目录。')
        return dest

    def read_json(self, relative):
        return json.loads(self.path(relative).read_text(encoding='utf-8'))

    def write_json(self, relative, value):
        atomic_json(self.path(relative), value)

    def task(self):
        task = self.read_json('task.json')
        if task.get('schema_version') != self.SCHEMA:
            raise NeedsInput('此任务版本不受当前程序支持。')
        return task

    def plan(self):
        plan = self.read_json(self.task().get('plan_path', 'plan.json'))
        if fingerprint(plan) != self.task()['plan_hash']:
            raise NeedsInput('任务计划已变化或损坏，请重新建立任务。')
        seen = set()
        for step in plan['steps']:
            sid = step['id']
            if sid in seen or not set(step['dependencies']+step.get('length_dependencies',[])).issubset(seen):
                raise NeedsInput('步骤依赖无效或存在重复编号。')
            seen.add(sid)
        return plan

    @contextmanager
    def lock(self):
        """OS-held lock: process termination automatically releases ownership."""
        lock_path = self.path('.writer.lock')
        stream = lock_path.open('a+b')
        if lock_path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        locked = False
        try:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                raise BusyError('该任务正在另一个进程中执行。') from exc
            yield
        finally:
            if locked:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()

    def event(self, event, **fields):
        path = self.path('logs/events.jsonl')
        record = {'at': now(), 'event_id': uuid.uuid4().hex, 'event': event, **fields}
        with path.open('a+b') as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell():
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b'\n':
                    stream.write(b'\n')
            stream.write((json.dumps(record, ensure_ascii=False) + '\n').encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())

    def new_attempt(self, step_id):
        folder = self.path(f'steps/{step_id}/attempts')
        folder.mkdir(parents=True, exist_ok=True)
        indexes = [int(p.name) for p in folder.iterdir() if p.is_dir() and p.name.isdigit()]
        dest = folder / f'{max(indexes, default=0)+1:04d}'
        dest.mkdir()
        return dest

    def committed(self, step, input_hash):
        try:
            record = self.read_json(f"steps/{step['id']}/commit.json")
            if record['input_hash'] != input_hash or record['step_id'] != step['id']:
                return None
            artifact = record['artifact']
            if file_hash(self.path(artifact['path'])) != artifact['sha256']:
                return None
            for extra in record.get('attachments',[]):
                if file_hash(self.path(extra['path'])) != extra['sha256']:
                    return None
            return record
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def commit(self, step, input_hash, artifact, run_id):
        artifact = Path(artifact).resolve()
        rel = artifact.relative_to(self.root).as_posix()
        # Check containment again through path() to reject escaping links.
        record = {'schema_version': 1, 'step_id': step['id'], 'run_id': run_id,
                  'input_hash': input_hash, 'at': now(),
                  'artifact': {'path': rel, 'sha256': file_hash(self.path(rel)), 'bytes': artifact.stat().st_size}}
        extras=[]
        attachment_manifest=artifact.parent/'attachments.json'
        if attachment_manifest.exists():
            extras.append(attachment_manifest)
            extras.extend(p for p in (artifact.parent/'files').rglob('*') if p.is_file())
        record['attachments']=[{'path':p.relative_to(self.root).as_posix(),'sha256':file_hash(p)} for p in extras]
        self.write_json(f"steps/{step['id']}/result.json", record)
        self.write_json(f"steps/{step['id']}/commit.json", record)
        return record

    def update_plan(self,plan):
        digest=fingerprint(plan)
        path=f'plans/{digest}.json'
        self.write_json(path,plan)
        task=self.task()
        task.update(plan_path=path,plan_hash=digest)
        self.write_json('task.json',task)

    def request_pause(self):
        state = self.read_json('state.json')
        self.write_json('control.json', {'action':'pause', 'run_id':state.get('run_id'), 'at':now()})
        return state.get('run_id')
