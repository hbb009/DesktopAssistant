import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from pages.page_dup_clean import _send_to_recycle, _still_safe_to_delete, plan_same_size_deletes, scan_tree
from utils import app_paths, data_backup, downloaded_records, prefs_secrets
from utils.user_prefs import default_prefs


class DataSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.roots = [(name, self.root / name) for name in ("userdata", "log", "gameassist", "gamebrain")]
        self.userdata = self.root / "userdata"
        self.userdata.mkdir()
        self.backup = self.root / "backup.zip"
        self.prefs = default_prefs()

    def restore(self):
        return data_backup.restore_backup(
            self.backup, self.roots, self.prefs, before_replace=lambda: None, after_replace=lambda: None
        )

    def archive(self, entries):
        with zipfile.ZipFile(self.backup, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)

    def test_latest_and_legacy_backups_restore_to_userdata(self):
        for layout in ("userdata", "data", "records", "backup-wrapper/data"):
            with self.subTest(layout=layout):
                self.archive({
                    f"{layout}/user.txt": json.dumps(self.prefs),
                    f"{layout}/paste_helper.txt": "backup records",
                    f"{layout}/pre_download.json": '{"queues":{"douyin":["https://example.test/video"]}}',
                })
                self.restore()
                self.assertEqual((self.userdata / "paste_helper.txt").read_text(), "backup records")
                self.assertTrue((self.userdata / "user.txt").is_file())
                self.assertIn("example.test", (self.userdata / "pre_download.txt").read_text())

    def test_corrupt_or_unrecognized_backup_leaves_existing_files(self):
        original = self.userdata / "paste_helper.txt"
        original.write_text("before")
        for entries in ({"misc/readme.txt": "unrelated"}, {"userdata/user.txt": "invalid JSON"}):
            with self.subTest(entries=entries):
                self.archive(entries)
                with self.assertRaises(ValueError):
                    self.restore()
                self.assertEqual(original.read_text(), "before")

    def test_partial_directory_swap_is_rolled_back(self):
        (self.userdata / "paste_helper.txt").write_text("before")
        kb = self.root / "gameassist"
        kb.mkdir()
        (kb / "index.json").write_text("original")
        self.archive({"userdata/paste_helper.txt": "after", "gameassist/index.json": "replacement"})
        rename = Path.rename

        def fail_second_install(source, destination):
            if source.name == "gameassist" and source.parent.name == "ready":
                raise OSError("simulated write failure")
            return rename(source, destination)

        with patch.object(Path, "rename", fail_second_install):
            with self.assertRaisesRegex(OSError, "simulated"):
                self.restore()
        self.assertEqual((self.userdata / "paste_helper.txt").read_text(), "before")
        self.assertEqual((kb / "index.json").read_text(), "original")

    def test_restore_keeps_the_selected_archive_inside_userdata(self):
        self.backup = self.userdata / "selected.zip"
        self.archive({"userdata/paste_helper.txt": "after"})
        archive_bytes = self.backup.read_bytes()
        self.restore()
        self.assertEqual(self.backup.read_bytes(), archive_bytes)
        self.assertEqual((self.userdata / "paste_helper.txt").read_text(), "after")

    def test_legacy_startup_migrates_records_and_keeps_original(self):
        for name in ("data", "records", "log"):
            source = self.root / name
            source.mkdir()
            (source / "paste_helper.txt").write_text("legacy")
            with patch.object(app_paths, "app_root", return_value=str(self.root)), patch.object(
                app_paths, "_MIGRATED_USERDATA", False
            ):
                app_paths._migrate_userdata_once()
            record = self.userdata / "paste_helper.txt"
            self.assertEqual(record.read_text(), "legacy")
            self.assertTrue((source / "paste_helper.txt").exists())
            record.unlink()

    def test_save_does_not_overwrite_download_records_on_read_failure(self):
        path = self.userdata / "downloaded.txt"
        original = "# hitomi\nprevious.zip\n"
        path.write_text(original)
        with patch.object(app_paths, "app_root", return_value=str(self.root)), patch.object(
            downloaded_records, "_read_text", side_effect=OSError("read failed")
        ):
            self.assertFalse(downloaded_records.write_sections_text(("e-hentai",), "# e-hentai\nnew\n"))
        self.assertEqual(path.read_text(), original)

    def test_download_migration_keeps_old_file_on_read_failure(self):
        legacy = self.userdata / "gallery_eh.txt"
        legacy.write_text("previous")
        read = downloaded_records._read_text

        def fail_old(path, **kwargs):
            if Path(path) == legacy:
                raise OSError("read failed")
            return read(path, **kwargs)

        with patch.object(app_paths, "app_root", return_value=str(self.root)), patch.object(
            downloaded_records, "_read_text", side_effect=fail_old
        ):
            self.assertFalse(downloaded_records.migrate_legacy_download_records())
        self.assertTrue(legacy.is_file())
        self.assertFalse((self.userdata / "downloaded.txt").exists())

    def test_same_size_different_content_is_not_eligible_for_bulk_deletion(self):
        for name, body in (("one", b"abc"), ("two", b"xyz")):
            directory = self.root / name
            directory.mkdir()
            (directory / "same.txt").write_bytes(body)
        keep, victims = plan_same_size_deletes(scan_tree(str(self.root)).by_name)[0]
        self.assertEqual(_still_safe_to_delete(keep, victims[0]), "文件内容不同")

    @unittest.skipUnless(hasattr(__import__("ctypes"), "windll"), "Windows recycle bin")
    def test_recycle_failure_does_not_delete_original(self):
        import ctypes
        path = self.root / "original.txt"
        path.write_text("keep")
        with patch.object(ctypes.windll.shell32, "SHFileOperationW", return_value=5):
            with self.assertRaises(OSError):
                _send_to_recycle(str(path))
        self.assertTrue(path.exists())

    def test_portable_archive_omits_keys_and_restore_retains_matching_key(self):
        self.prefs["ollama_chat"] = {"api_base": "https://example.test/v1", "api_key": "test-secret"}
        (self.userdata / "user.txt").write_text(json.dumps(self.prefs), encoding="utf-8")
        self.assertEqual(data_backup.export_backup(self.backup, self.roots), 1)
        with zipfile.ZipFile(self.backup) as archive:
            self.assertNotIn(b"test-secret", archive.read("userdata/user.txt"))
        with patch("win32crypt.CryptProtectData", return_value=b"opaque-test-blob") as encrypt:
            self.restore()
        encrypt.assert_called_once()
        saved = (self.userdata / "user.txt").read_text(encoding="utf-8")
        self.assertNotIn("test-secret", saved)
        self.assertIn("api_key_dpapi", saved)
        payload = json.loads(saved)
        with patch("win32crypt.CryptUnprotectData", return_value=("", b"test-secret")):
            prefs_secrets.unlock_api_keys(payload)
        self.assertEqual(payload["ollama_chat"]["api_key"], "test-secret")

    def test_export_failure_keeps_existing_archive(self):
        self.backup.write_bytes(b"existing archive")
        (self.userdata / "user.txt").write_text("not valid JSON")
        with self.assertRaises(ValueError):
            data_backup.export_backup(self.backup, self.roots)
        self.assertEqual(self.backup.read_bytes(), b"existing archive")
