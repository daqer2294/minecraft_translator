"""Замена файла с повтором: на Windows её на мгновение блокирует антивирус или Minecraft."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from src.utils import helpers
from src.utils.cache import TranslationCache


class Replace(unittest.TestCase):
    def _files(self):
        folder = tempfile.mkdtemp()
        src, dst = os.path.join(folder, "a.tmp"), os.path.join(folder, "a")
        with open(src, "w", encoding="utf-8") as f:
            f.write("новое")
        with open(dst, "w", encoding="utf-8") as f:
            f.write("старое")
        return src, dst

    def test_short_lock_is_waited_out(self):
        src, dst = self._files()
        real, calls = os.replace, []

        def flaky(a, b):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(13, "файл занят другим процессом")
            real(a, b)

        with mock.patch.object(helpers.os, "replace", side_effect=flaky), mock.patch("time.sleep") as sleep:
            helpers.replace_file(src, dst)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)
        with open(dst, encoding="utf-8") as f:
            self.assertEqual(f.read(), "новое")

    def test_long_lock_raises_as_before(self):
        src, dst = self._files()
        with mock.patch.object(helpers.os, "replace", side_effect=PermissionError(13, "занят")), \
                mock.patch("time.sleep"):
            with self.assertRaises(PermissionError):
                helpers.replace_file(src, dst, attempts=3)
        with open(dst, encoding="utf-8") as f:
            self.assertEqual(f.read(), "старое")

    def test_other_errors_are_not_retried(self):
        src, dst = self._files()
        with mock.patch.object(helpers.os, "replace", side_effect=FileNotFoundError()) as rep, mock.patch("time.sleep"):
            with self.assertRaises(FileNotFoundError):
                helpers.replace_file(src, dst)
        self.assertEqual(rep.call_count, 1)

    def test_cache_survives_a_short_lock(self):
        path = os.path.join(tempfile.mkdtemp(), "c.json")
        cache = TranslationCache(path)
        cache.put("Iron Ingot", "Железный слиток", "ru_ru")
        real, calls = os.replace, []

        def flaky(a, b):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError(13, "антивирус проверяет файл")
            real(a, b)

        with mock.patch.object(helpers.os, "replace", side_effect=flaky), mock.patch("time.sleep"):
            cache.flush()
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["ru_ru"]["Iron Ingot"], "Железный слиток")


if __name__ == "__main__":
    unittest.main()
