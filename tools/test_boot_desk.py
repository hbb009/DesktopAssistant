# 开机员：网址、快捷方式文件名、启动开关字节，以及页面能建出来。
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import boot_desk
from utils.user_prefs import DEFAULT_SIDE_ORDER


class TestBootDesk(unittest.TestCase):
    def test_normalize_url(self):
        self.assertEqual(boot_desk.normalize_url("  example.com/a "), "https://example.com/a")
        self.assertEqual(boot_desk.normalize_url("http://a.com"), "http://a.com")
        self.assertEqual(boot_desk.normalize_url(""), "")

    def test_lnk_basename_drops_directories(self):
        self.assertEqual(boot_desk.safe_lnk_basename(r"..\Windows\notepad.lnk"), "notepad.lnk")
        self.assertEqual(boot_desk.safe_lnk_basename("notepad.exe"), "")
        self.assertEqual(boot_desk.safe_lnk_basename("微信.lnk"), "微信.lnk")
        root = os.path.normcase(os.path.abspath(boot_desk.shortcuts_dir()))
        path = os.path.normcase(os.path.abspath(boot_desk.shortcut_path(r"..\..\微信.lnk")))
        self.assertTrue(path.startswith(root + os.sep))

    def test_startup_flag(self):
        self.assertTrue(boot_desk.startup_enabled_from_blob(None))
        self.assertTrue(boot_desk.startup_enabled_from_blob(bytes([2, 0, 0, 0])))
        self.assertFalse(boot_desk.startup_enabled_from_blob(bytes([3, 0, 0, 0])))
        blob = boot_desk.startup_blob(False)
        self.assertEqual(blob[0], 3)
        self.assertEqual(len(blob), 12)
        self.assertFalse(boot_desk.startup_enabled_from_blob(blob))

    def test_unique_name_and_shortcut_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            first = boot_desk.unique_lnk_name(folder, "记事本")
            open(os.path.join(folder, first), "wb").close()
            second = boot_desk.unique_lnk_name(folder, "记事本")
            self.assertNotEqual(first, second)
            dest = os.path.join(folder, "开机员测试.lnk")
            target = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "notepad.exe")
            if not os.path.isfile(target):
                self.skipTest("没有 notepad.exe")
            boot_desk.create_shortcut(dest, target, os.path.dirname(target))
            self.assertTrue(os.path.isfile(dest))
            got = boot_desk.read_shortcut_target(dest)
            self.assertTrue(got.lower().endswith("notepad.exe"))

    def test_list_startup_is_a_list(self):
        items = boot_desk.list_startup_items()
        self.assertIsInstance(items, list)
        for item in items:
            self.assertIn("name", item)
            self.assertIn("kind", item)
            self.assertIn("enabled", item)

    def test_side_order_boot_before_paste(self):
        self.assertLess(DEFAULT_SIDE_ORDER.index("boot"), DEFAULT_SIDE_ORDER.index("paste"))


class TestBootPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_roundtrip_and_icon(self):
        from pages.page_boot import PageBoot
        from styles.side_nav_icons import paint_side_nav_icon
        page = PageBoot()
        page.apply_settings({
            "dirs": [{"name": "文章", "path": r"D:\文章"}],
            "sites": [{"name": "B站", "url": "bilibili.com"}],
            "apps": [{"name": "坏路径", "lnk": r"..\nope.lnk"}],
        })
        data = page.export_settings()
        self.assertEqual(data["dirs"][0]["name"], "文章")
        self.assertEqual(data["sites"][0]["url"], "https://bilibili.com")
        self.assertEqual(data["apps"][0]["lnk"], "nope.lnk")
        from PyQt5.QtWidgets import QFrame
        self.assertEqual(len(page.findChildren(QFrame, "BootDirCard")), 1)
        self.assertEqual(len(page.findChildren(QFrame, "BootSiteCard")), 1)
        self.assertEqual(page.btn_startup.text(), "启动项")
        self.assertEqual(page.btn_rename.text(), "改名")
        self.assertEqual(page.btn_delete.text(), "删除")
        self.assertFalse(paint_side_nav_icon("boot", 20).isNull())
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
