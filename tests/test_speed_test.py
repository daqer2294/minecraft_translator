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

        with mock.patch.object(config, "APP_DATA_DIR", home), mock.patch.object(speed_test, "run_profile", side_effect=fake), \
                mock.patch.object(speed_test, "strong_threads", return_value=[]), \
                mock.patch.object(speed_test, "run_ollama_profile", side_effect=speed_test.Skipped("Ollama не отвечает")), \
                mock.patch.object(speed_test.hp, "gpu_offload_supported", return_value=True):
            path = speed_test.report(pack(), limit=4)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(path, os.path.join(home, "speed_test.txt"))
        self.assertIn("[auto]", text)
        self.assertIn("strings_per_minute: 42.0", text)
        self.assertIn("[fast_cpu] ОШИБКА: RuntimeError: нет памяти", text)
        self.assertIn("Строк: 4", text)
        self.assertIn("[ollama] Только Ollama: основная и сильная модели в Ollama, как в режиме «Локальная Ollama»: "
                      "пропущено, Ollama не отвечает", text)

    def test_bundled_set_has_70_short_and_30_long(self):
        from src.utils.markup import text_word_count
        texts = speed_test.bundled_strings()
        self.assertEqual(len(texts), 100)
        self.assertEqual(sum(text_word_count(t) >= 15 for t in texts), 30)
        with open(speed_test.BUNDLED_PATH, encoding="utf-8") as f:
            packs = {item["pack"] for item in json.load(f)["strings"]}
        self.assertEqual(packs, {"Cobblemon SMP", "ATM10"})

    def test_no_path_uses_the_bundled_set(self):
        home = tempfile.mkdtemp()
        seen = []
        with mock.patch.object(config, "APP_DATA_DIR", home), \
                mock.patch.object(speed_test, "run_profile", side_effect=lambda p, s, b, log: seen.append((b, len(s))) or {"x": 1}), \
                mock.patch.object(speed_test, "strong_threads", return_value=["потоки: пропущено"]), \
                mock.patch.object(speed_test, "run_ollama_profile", side_effect=lambda s, b, log, m: seen.append(("ollama", len(s))) or {"x": 1}), \
                mock.patch.object(speed_test.hp, "gpu_offload_supported", return_value=True):
            path = speed_test.cli(["exe", "--speed-test", "--limit", "10"])
        self.assertEqual(seen, [(None, 10), (None, 10), ("ollama", 10)])
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("встроенный набор", text)
        self.assertIn("потоки: пропущено", text)

    def test_bundled_file_ships_with_the_app(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for spec in ("MinecraftTranslator-mac.spec", "MinecraftTranslator-win.spec"):
            with open(os.path.join(root, spec), encoding="utf-8") as f:
                self.assertIn('("src/data", "src/data")', f.read(), spec)

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


class Sampler(unittest.TestCase):
    """«TypeError: 'Event' object is not callable» на Windows: поле перекрывало Thread._stop() из Python 3.11."""

    def test_no_attribute_shadows_thread_internals(self):
        import threading
        sampler = speed_test.VramSampler()
        own = set(vars(sampler)) - set(vars(threading.Thread()))
        thread_names = {n for n in dir(threading.Thread)} | {"_stop", "_started", "_is_stopped", "_tstate_lock"}
        self.assertEqual(own & thread_names, set())

    def test_join_after_stop_without_nvidia(self):
        with mock.patch.object(speed_test.subprocess, "run", side_effect=FileNotFoundError()):
            sampler = speed_test.VramSampler()
            sampler.start()
            sampler.stop()
            sampler.join(3)
        self.assertFalse(sampler.is_alive())
        self.assertEqual(sampler.samples, [])

    def test_python_311_like_in_ci(self):
        import shutil
        import subprocess as sp
        py = shutil.which("python3.11") or ("/usr/local/bin/python3.11" if os.path.exists("/usr/local/bin/python3.11") else None)
        if not py:
            self.skipTest("нет python3.11")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = (
            "import ast, sys, threading, subprocess, types\n"
            f"src = open({os.path.join(root, 'src', 'speed_test.py')!r}, encoding='utf-8').read()\n"
            "node = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == 'VramSampler')\n"
            "ns = {'threading': threading, 'subprocess': subprocess, 'List': list, 'hp': types.SimpleNamespace(_run_quiet_kwargs=lambda: {})}\n"
            "exec(compile(ast.Module([node], []), 'speed_test.py', 'exec'), ns)\n"
            "s = ns['VramSampler'](); s.start(); s.stop(); s.join(3); print('OK')\n"
        )
        out = sp.run([py, "-c", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.strip(), "OK", out.stderr)
