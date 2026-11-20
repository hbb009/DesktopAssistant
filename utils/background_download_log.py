"""Local diagnostic history for the image background download mode."""

from datetime import datetime
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time

from utils.photo_viewer_cfg import prefs_file_lock


BACKGROUND_STEPS = (
    ("shortcut", "收到 Alt+1"),
    ("source", "识别图链"),
    ("process", "启动进程"),
    ("transfer", "下载图片"),
    ("validate", "校验格式"),
    ("save", "保存文件"),
)
_MAX_OPERATIONS = 20


def _timestamp():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _recent(records):
    return sorted(records, key=lambda entry: entry["started_ns"])[-_MAX_OPERATIONS:]


class BackgroundDownloadLog:
    def __init__(self, path, logger):
        self.path = Path(path)
        self._logger = logger
        self._unwritten = {}
        self._own_operations = set()
        self._write_failed = False
        try:
            self.records = self._read()
        except OSError:
            self.records = []
            self._logger.exception("读取后台下载日志失败")

    def _read(self):
        records = []
        try:
            with self.path.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if (
                        isinstance(entry, dict) and isinstance(entry.get("id"), str)
                        and isinstance(entry.get("started_ns"), int) and isinstance(entry.get("started_at"), str)
                        and entry.get("status") in ("pending", "ok", "failed") and isinstance(entry.get("steps"), dict)
                        and all(isinstance(step, dict) and step.get("status") in ("pending", "ok", "failed", "skipped")
                                and isinstance(step.get("detail", ""), str)
                                for step in entry["steps"].values())
                    ):
                        records.append(entry)
        except FileNotFoundError:
            pass
        return _recent(records)

    def _commit(self, changes):
        self._unwritten.update((entry["id"], entry) for entry in changes)
        current = {entry["id"]: entry for entry in self.records}
        current.update(self._unwritten)
        self.records = _recent(current.values())
        self._unwritten = {entry["id"]: entry for entry in self.records if entry["id"] in self._unwritten}
        self._own_operations.intersection_update(entry["id"] for entry in self.records)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with prefs_file_lock(str(self.path)) as lock:
                if not lock.held:
                    raise OSError("无法锁定后台下载日志")
                saved = {entry["id"]: entry for entry in self._read()}
                saved.update(self._unwritten)
                recent = _recent(saved.values())
                temp = None
                try:
                    with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=self.path.parent,
                                            prefix="background-image-download-", suffix=".tmp", delete=False) as stream:
                        temp = Path(stream.name)
                        for entry in recent:
                            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    os.replace(temp, self.path)
                finally:
                    if temp is not None:
                        temp.unlink(missing_ok=True)
            self.records = recent
            self._unwritten.clear()
            self._write_failed = False
        except OSError:
            if not self._write_failed:
                self._logger.exception("写入后台下载日志失败")
            self._write_failed = True

    def start(self, operation_id, browser):
        now = _timestamp()
        steps = {key: {"status": "pending"} for key, _ in BACKGROUND_STEPS}
        steps["shortcut"] = {"status": "ok", "time": now, "detail": f"Alt+1 · {browser}"}
        self._own_operations.add(operation_id)
        self._commit([{
            "id": operation_id, "started_at": now, "started_ns": time.time_ns(),
            "browser": browser, "status": "pending", "steps": steps,
        }])

    def find(self, operation_id):
        return next((entry for entry in self.records if entry["id"] == operation_id), None)

    @staticmethod
    def _with_step(record, step, passed, detail):
        steps = dict(record["steps"])
        steps[step] = {"status": "ok" if passed else "failed", "time": _timestamp(), "detail": detail}
        if not passed:
            for key, value in steps.items():
                if value.get("status") == "pending":
                    steps[key] = {"status": "skipped"}
        status = "failed" if not passed else "ok" if step == "save" else "pending"
        return {**record, "status": status, "steps": steps}

    def step(self, operation_id, step, passed, detail=""):
        record = self.find(operation_id)
        if record and record["status"] == "pending" and record["steps"].get(step, {}).get("status") == "pending":
            self._commit([self._with_step(record, step, passed, detail)])
            return True
        return False

    def interrupt(self, detail, operations=None):
        operations = self._own_operations if operations is None else operations
        changes = []
        for record in self.records:
            if record["id"] not in operations or record["status"] != "pending":
                continue
            step = next((key for key, _ in BACKGROUND_STEPS if record["steps"].get(key, {}).get("status") == "pending"), None)
            if step:
                changes.append(self._with_step(record, step, False, detail))
        if changes:
            self._commit(changes)
        return bool(changes)
