"""Оконный exe на Windows: потоки вывода и проверка WebView2."""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

from src import platform_guard


class Streams(unittest.TestCase):
    def test_none_streams_become_devnull(self):
        with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
            platform_guard.ensure_std_streams()
            self.assertIsNotNone(sys.stdout)
            print("никуда", file=sys.stdout)
            sys.stderr.write("никуда")

    def test_closed_descriptor_is_reopened(self):
        real_fstat = os.fstat
        dup2 = []

        def fstat(fd):
            if fd == 1:
                raise OSError(9, "Bad file descriptor")
            return real_fstat(fd)

        with mock.patch.object(os, "fstat", side_effect=fstat), \
                mock.patch.object(os, "dup2", side_effect=lambda a, b: dup2.append(b)):
            self.assertEqual(platform_guard.ensure_std_streams(), [1])
        self.assertEqual(dup2, [1])

    def test_normal_process_is_left_alone(self):
        out = sys.stdout
        with mock.patch.object(os, "dup2") as dup2:
            self.assertEqual(platform_guard.ensure_std_streams(), [])
        dup2.assert_not_called()
        self.assertIs(sys.stdout, out)


class WebView2(unittest.TestCase):
    def _with_winforms(self, is_chromium):
        module = types.ModuleType("webview.platforms.winforms")
        module.is_chromium = is_chromium
        return mock.patch.dict(sys.modules, {"webview.platforms.winforms": module})

    def test_not_windows_never_blocks(self):
        with mock.patch.object(platform_guard.platform, "system", return_value="Darwin"), self._with_winforms(False):
            self.assertFalse(platform_guard.webview2_missing())

    def test_windows_without_webview2(self):
        with mock.patch.object(platform_guard.platform, "system", return_value="Windows"), self._with_winforms(False):
            self.assertTrue(platform_guard.webview2_missing())

    def test_windows_with_webview2(self):
        with mock.patch.object(platform_guard.platform, "system", return_value="Windows"), self._with_winforms(True):
            self.assertFalse(platform_guard.webview2_missing())

    def test_check_failure_does_not_block_start(self):
        broken = types.ModuleType("webview.platforms.winforms")
        with mock.patch.object(platform_guard.platform, "system", return_value="Windows"), \
                mock.patch.dict(sys.modules, {"webview.platforms.winforms": broken}):
            self.assertFalse(platform_guard.webview2_missing())

    def test_interface_really_needs_a_modern_engine(self):
        web = os.path.join(os.path.dirname(platform_guard.__file__), "gui", "web")
        with open(os.path.join(web, "app.js"), encoding="utf-8") as f:
            js = f.read()
        with open(os.path.join(web, "style.css"), encoding="utf-8") as f:
            css = f.read()
        self.assertIn("async ", js)
        self.assertIn("var(--", css)


if __name__ == "__main__":
    unittest.main()
