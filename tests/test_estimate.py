"""«Только посчитать»: тот же конвейер, но без модели и без записи."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

from src import config, estimate

LONG = "It climbs trees by hooking on with its sharp claws and shares the berries it gathers with others."


def make_pack(base):
    os.makedirs(os.path.join(base, "mods"))
    with zipfile.ZipFile(os.path.join(base, "mods", "a.jar"), "w") as z:
        z.writestr("assets/a/lang/en_us.json", json.dumps({
            "short": "Iron Ingot", "again": "Iron Ingot", "mid": "Opens the settings of this mod", "long": LONG,
            "num": "123 %s", "done": "Gold Ingot", "official": "Copper Ingot", "id": "minecraft:stone",
        }))
        z.writestr("assets/a/lang/ru_ru.json", json.dumps({"official": "Медный слиток"}, ensure_ascii=False))
    with zipfile.ZipFile(os.path.join(base, "mods", "b.jar"), "w") as z:
        z.writestr("assets/b/lang/en_us.json", json.dumps({"same": "Iron Ingot", "other": "Silver Ingot"}))


class Count(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        make_pack(self.base)
        self.cache = os.path.join(tempfile.mkdtemp(), "c.json")
        with open(self.cache, "w", encoding="utf-8") as f:
            json.dump({"ru_ru": {"Gold Ingot": "Золотой слиток"}}, f, ensure_ascii=False)
        with open(self.cache, "rb") as f:
            self.before = f.read()

    def run_estimate(self, strong=True):
        return estimate.estimate_dir(self.base, "ru_ru", self.cache, strong_available=strong, log=lambda s: None)

    def test_counts_by_kind(self):
        e = self.run_estimate()
        self.assertEqual(e.total, 9)
        self.assertEqual(e.cached, 1)
        self.assertEqual(e.nothing_to_translate, 2)
        self.assertEqual(e.repeated, 2)
        self.assertEqual(e.unique, 4)
        self.assertEqual(sum(e.to_fast.values()), 3)
        self.assertEqual(e.to_strong, {"до 3 слов": 0, "4–14 слов": 0, "15+ слов": 1})
        self.assertEqual(e.to_fast["до 3 слов"], 2)
        self.assertEqual(e.to_fast["4–14 слов"], 1)
        self.assertEqual(e.fast_requests, 3)
        self.assertEqual(e.strong_requests, 1)

    def test_without_strong_model_long_goes_to_the_fast_one(self):
        e = self.run_estimate(strong=False)
        self.assertEqual(sum(e.to_strong.values()), 0)
        self.assertEqual(e.to_fast["15+ слов"], 1)
        self.assertEqual(e.second_pass, 0)

    def test_nothing_is_written(self):
        self.run_estimate()
        with open(self.cache, "rb") as f:
            self.assertEqual(f.read(), self.before)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.cache))), ["c.json"])

    def test_time_and_lines(self):
        e = self.run_estimate()
        lines = e.lines()
        self.assertTrue(any(line.startswith("Строк всего: 9, разных: 4") for line in lines))
        self.assertTrue(any("Оценка времени" in line for line in lines))
        expected = 2 * estimate.MEASURED_SECONDS["fast"]["до 3 слов"] + estimate.MEASURED_SECONDS["fast"]["4–14 слов"]
        self.assertAlmostEqual(e.seconds["fast"], expected)
        self.assertAlmostEqual(e.seconds["strong"], estimate.MEASURED_SECONDS["strong"]["15+ слов"])

    def test_buckets(self):
        self.assertEqual(estimate.bucket("Iron Ingot"), "до 3 слов")
        self.assertEqual(estimate.bucket("Opens the settings of this mod"), "4–14 слов")
        self.assertEqual(estimate.bucket(LONG), "15+ слов")

    def test_loose_lang_file_goes_in_batches(self):
        folder = os.path.join(self.base, "resourcepacks", "loose", "assets", "c", "lang")
        os.makedirs(folder)
        with open(os.path.join(folder, "en_us.json"), "w") as f:
            json.dump({"x": "Wooden Door", "y": "Stone Wall"}, f)
        with mock.patch.object(config, "BATCH_SIZE", 25):
            e = self.run_estimate()
        self.assertEqual(sum(e.to_fast_batched.values()), 2)


class Api(unittest.TestCase):
    def test_dry_run_counts_without_a_model(self):
        from src.gui.api import Api
        base = tempfile.mkdtemp()
        make_pack(base)
        api = Api.__new__(Api)
        api.__init__()
        api._state["input"] = base
        api._state["dry"] = True
        with mock.patch.object(config, "DEFAULT_CACHE_PATH", os.path.join(tempfile.mkdtemp(), "c.json")), \
                mock.patch("src.llm.factory.build_strong_client", return_value=(None, "нет")), \
                mock.patch.object(Api, "_missing_specs", side_effect=AssertionError("модель не нужна")):
            api.start()
            api._worker.join(30)
        snap = api._snapshot()
        self.assertEqual(snap["phase"], "done")
        self.assertTrue(any(line.startswith("Строк всего:") for line in snap["estimate"]))


if __name__ == "__main__":
    unittest.main()
