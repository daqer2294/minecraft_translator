"""По умолчанию строки идут по одной: так быстрее и точнее на замере. Пачки включаются BATCH_SIZE."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from src import config
from src.translators import Translator
from src.utils.cache import TranslationCache


class Model:
    name = "m"

    def __init__(self):
        self.single, self.batches = [], []

    def chat(self, messages, **kwargs):
        text = messages[-1]["content"]
        if "JSON array" in messages[0]["content"]:
            items = json.loads(text)
            self.batches.append(items)
            return json.dumps(["ПЕР " + x for x in items], ensure_ascii=False)
        self.single.append(text)
        return "ПЕР " + text


def run(texts):
    model = Model()
    tr = Translator(model, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                    log=lambda s: None, target_lang="ru_ru")
    with contextlib.redirect_stdout(io.StringIO()):
        out = tr.translate_many(texts)
    return model, out


class Default(unittest.TestCase):
    def test_default_is_one_string_per_request(self):
        self.assertEqual(config.BATCH_SIZE, 1)
        model, out = run(["Iron Ingot", "Gold Ingot", "Iron Ingot"])
        self.assertEqual(model.batches, [])
        self.assertEqual(model.single, ["Iron Ingot", "Gold Ingot"])
        self.assertEqual(out, ["ПЕР Iron Ingot", "ПЕР Gold Ingot", "ПЕР Iron Ingot"])

    def test_batches_can_be_turned_on(self):
        with mock.patch.object(config, "BATCH_SIZE", 25):
            model, out = run(["Iron Ingot", "Gold Ingot"])
        self.assertEqual(model.batches, [["Iron Ingot", "Gold Ingot"]])
        self.assertEqual(model.single, [])


if __name__ == "__main__":
    unittest.main()
