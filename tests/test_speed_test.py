"""Тест скорости для Windows: выбор строк, отчёт в файл, запуск без окна."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

from src import config, speed_test

LONG = "It climbs trees by hooking on with its sharp claws and shares the berries it gathers with others."


def pack():
    base = tempfile.mkdtemp()
    os.makedirs(os.path.join(base, "mods"))
    data = {f"s{i}": f"Item number {chr(65 + i)}" for i in range(10)}
    data.update({f"l{i}": f"{LONG} Part {chr(65 + i)}." for i in range(5)})
    data.update({"id": "minecraft:stone", "num": "123"})
    with zipfile.ZipFile(os.path.join(base, "mods", "a.jar"), "w") as z:
        z.writestr("assets/a/lang/en_us.json", json.dumps(data))
        z.writestr("assets/a/lang/ru_ru.json", json.dumps({}))
    return base


class Strings(unittest.TestCase):
    def test_mix_of_short_and_long_without_codes(self):
        texts = speed_test.collect_strings(pack(), n_short=4, n_long=2)
        self.assertEqual(len(texts), 6)
        self.assertEqual(sum(t.startswith(LONG) for t in texts), 2)
        self.assertNotIn("minecraft:stone", texts)
        self.assertNotIn("123", texts)

    def test_same_strings_every_run(self):
        base = pack()
        self.assertEqual(speed_test.collect_strings(base, 4, 2), speed_test.collect_strings(base, 4, 2))


class Report(unittest.TestCase):
    def test_report_lists_both_profiles_and_survives_an_error(self):
        home = tempfile.mkdtemp()

        def fake(profile, strings, base, log):
            if profile == "fast_cpu":
                raise RuntimeError("нет памяти")
            return {"profile": profile, "strings": len(strings), "strings_per_minute": 42.0}

        with mock.patch.object(config, "APP_DATA_DIR", home), mock.patch.object(speed_test, "run_profile", side_effect=fake):
            path = speed_test.report(pack(), limit=4)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(path, os.path.join(home, "speed_test.txt"))
        self.assertIn("[auto]", text)
        self.assertIn("strings_per_minute: 42.0", text)
        self.assertIn("[fast_cpu] ОШИБКА: RuntimeError: нет памяти", text)
        self.assertIn("Строк: 4", text)

    def test_missing_folder_is_reported(self):
        with self.assertRaises(SystemExit):
            speed_test.cli(["exe", "--speed-test", "/нет/такой/папки"])


class EntryPoint(unittest.TestCase):
    def test_flag_runs_the_test_instead_of_the_window(self):
        from src import main as entry
        with mock.patch.object(sys, "argv", ["exe", "--speed-test", "/нет/такой/папки"]), \
                mock.patch.dict(sys.modules, {"webview": None}), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            entry.main()
        self.assertIn("Тест скорости не выполнен", out.getvalue())


if __name__ == "__main__":
    unittest.main()
