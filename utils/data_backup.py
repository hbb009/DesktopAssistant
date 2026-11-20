"""Export and restore local data using staging directories on the destination volume."""
from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path, PureWindowsPath

from utils.app_paths import copy_legacy_userdata_file, legacy_userdata_entries
from utils.downloaded_records import FILE_NAME, merge_legacy_download_records
from utils.prefs_secrets import preserve_matching_keys, protect_api_keys, redact_api_keys
from utils.user_prefs import is_valid_user_prefs_payload


_LOG = logging.getLogger("deskassist.utils.data_backup")
_CURRENT_ROOTS = ("userdata", "log", "gameassist", "gamebrain")
_LAYOUTS = {*_CURRENT_ROOTS, "data", "records", "gallery", "cards"}


def _skip_file(name):
    name = name.casefold()
    return name.endswith((".tmp", ".lock")) or name.startswith("user.invalid.")


def export_backup(path, roots):
    path = Path(path).resolve()
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
        staged = Path(stream.name)
    count = 0
    try:
        with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as archive:
            for label, root in roots:
                root = Path(root)
                for directory, folders, files in os.walk(root):
                    folders[:] = [name for name in folders if not (Path(directory) / name).is_symlink()]
                    for name in files:
                        source = Path(directory) / name
                        if _skip_file(name) or source.is_symlink() or source.resolve() in (path, staged):
                            continue
                        relative = source.relative_to(root).as_posix()
                        member = f"{label}/{relative}"
                        if relative.casefold() == "user.txt":
                            prefs = json.loads(source.read_text(encoding="utf-8-sig"))
                            redact_api_keys(prefs)
                            archive.writestr(member, json.dumps(prefs, ensure_ascii=False, indent=2) + "\n")
                        else:
                            archive.write(source, member)
                        count += 1
        if not count:
            raise ValueError("没有可导出的本地数据。")
        os.replace(staged, path)
        return count
    finally:
        staged.unlink(missing_ok=True)


def _archive_parts(name):
    parts = name.replace("\\", "/").rstrip("/").split("/")
    for part in parts:
        if (not part or part in (".", "..") or part[-1] in " ."
                or any(c in '<>:"|?*' or ord(c) < 32 for c in part)
                or PureWindowsPath(part).is_reserved()):
            raise ValueError("备份包含无效文件路径。")
    return parts


def _extract(path, raw):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if sum(entry.file_size for entry in entries) > shutil.disk_usage(raw).free:
            raise OSError("磁盘空间不足，无法解压备份。")
        for entry in entries:
            parts = _archive_parts(entry.filename)
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("备份包含不支持的链接。")
            if entry.is_dir() or _skip_file(parts[-1]):
                continue
            target = raw.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def _entries(root):
    return {path.name.casefold(): path for path in root.iterdir()}


def _is_log(name):
    return name == "app.log" or name.startswith("app.log.") or (
        name.startswith("crash_") and name.endswith(".log")
    )


def _stage_restore(path, stage, local_prefs):
    raw, ready = stage / "raw", stage / "ready"
    raw.mkdir()
    ready.mkdir()
    _extract(path, raw)
    top = _entries(raw)
    if len(top) == 1:
        name, wrapper = next(iter(top.items()))
        if name not in _LAYOUTS and wrapper.is_dir():
            raw = wrapper
            top = _entries(raw)
    if not (_LAYOUTS.intersection(top) or list(legacy_userdata_entries(raw))
            or any(_is_log(name) for name in top)):
        raise ValueError("ZIP 内没有可识别的桌面助手数据。")

    for name in _CURRENT_ROOTS:
        origin = top.get(name)
        if origin is not None:
            if not origin.is_dir():
                raise ValueError(f"备份中的 {name} 应为目录。")
            origin.rename(ready / name)

    user = ready / "userdata"
    legacy_roots = [top[name] for name in ("records", "data") if name in top]
    legacy_roots.append(raw)
    if user.is_dir():
        legacy_roots.append(user)
    logs = ready / "log"
    if logs.is_dir():
        legacy_roots.append(logs)
    incoming = []
    for legacy in legacy_roots:
        incoming.extend(legacy_userdata_entries(legacy))
    if incoming:
        user.mkdir(exist_ok=True)
        # Canonical files take precedence, including over renamed variants.
        incoming.sort(key=lambda item: item[0].name.casefold() != item[1].name.casefold())
        for source, relative in incoming:
            destination = user / relative
            if not destination.exists():
                copy_legacy_userdata_file(source, destination)
    for legacy in legacy_roots:
        if legacy == user:
            continue
        for name, source in _entries(legacy).items():
            if source.is_file() and _is_log(name):
                logs = ready / "log"
                logs.mkdir(exist_ok=True)
                destination = logs / source.name
                if not destination.exists():
                    source.rename(destination)

    if not any(ready.iterdir()):
        raise ValueError("备份没有可恢复的文件。")
    if user.is_dir():
        merge_legacy_download_records(user / FILE_NAME, (user,))
        (user / (FILE_NAME + ".lock")).unlink(missing_ok=True)
        prefs_path = user / "user.txt"
        prefs = deepcopy(local_prefs)
        if prefs_path.is_file():
            prefs = json.loads(prefs_path.read_text(encoding="utf-8-sig"))
            if not is_valid_user_prefs_payload(prefs):
                raise ValueError("备份中的 user.txt 不是有效的桌面助手设置。")
            preserve_matching_keys(prefs, local_prefs)
        for name in ("gameassist", "gamebrain"):
            if (ready / name).exists():
                continue
            current = local_prefs.get(name)
            if not isinstance(current, dict):
                continue
            section = prefs.get(name)
            if not isinstance(section, dict):
                section = prefs[name] = {}
            section.update({key: current[key] for key in ("sources", "groups") if key in current})
        if prefs:
            protect_api_keys(prefs)
            prefs_path.write_text(json.dumps(prefs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ready


def _replace_directories(ready, rollback, locations):
    previous = []
    installed = []
    rollback.mkdir()
    try:
        replacements = [
            (ready / name, locations[name], rollback / name)
            for name in _CURRENT_ROOTS if name != "log" and (ready / name).exists()
        ]
        staged_logs = ready / "log"
        if staged_logs.is_dir():
            log_root = locations["log"]
            log_root.mkdir(exist_ok=True)
            (rollback / "log").mkdir()
            for source in staged_logs.iterdir():
                target = log_root / source.name
                # The running process keeps its crash log open on Windows.
                if source.is_file() and _is_log(source.name.casefold()):
                    if source.name.casefold().startswith("crash_") and target.exists():
                        continue
                    replacements.append((source, target, rollback / "log" / source.name))
        for staged, destination, prior in replacements:
            if destination.exists():
                destination.rename(prior)
                previous.append((prior, destination))
            staged.rename(destination)
            installed.append((destination, staged))
    except Exception as exc:
        rollback_errors = []
        for source, target in [*reversed(installed), *reversed(previous)]:
            try:
                source.rename(target)
            except OSError as failure:
                rollback_errors.append(failure)
        if rollback_errors:
            raise RestoreRollbackError(f"恢复中断，旧数据保留在 {rollback}") from exc
        raise


class RestoreRollbackError(OSError):
    """A rollback was interrupted and the previous directories must be retained."""


def restore_backup(path, roots, local_prefs, *, before_replace, after_replace):
    locations = {name: Path(root) for name, root in roots}
    app_root = locations["userdata"].parent
    stage = Path(tempfile.mkdtemp(prefix=".deskassist-restore-", dir=app_root))
    preserve_stage = False
    try:
        archive = Path(path).resolve()
        ready = _stage_restore(archive, stage, local_prefs)
        for name, destination in locations.items():
            if name == "log" or not (ready / name).is_dir():
                continue
            root = destination.resolve()
            if archive.is_relative_to(root):
                preserved = ready / name / archive.relative_to(root)
                preserved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(archive, preserved)
                break
        try:
            before_replace()
            _replace_directories(ready, stage / "rollback", locations)
        finally:
            try:
                after_replace()
            except Exception:
                _LOG.exception("恢复后重新打开日志失败")
    except RestoreRollbackError:
        preserve_stage = True
        raise
    finally:
        if not preserve_stage:
            shutil.rmtree(stage, ignore_errors=True)
