"""FTB Quests: ID, команды и данные предметов не должны попадать в модель."""
from __future__ import annotations

import os
import tempfile
import unittest

from src.processors.snbt_structured import translate_snbt_text_structured
from src.translators import Translator
from src.utils.cache import TranslationCache

CHAPTER = """{
    id: "0D595252CB72400E"
    title: "Real chapter title"
    quests: [{
        id: "504114DB16EDA4B3"
        dependencies: ["129E0E2085D16CB5", "22BB55249EF2E819"]
        dependency: "396CEB9D05700927"
        linked_quest: "196312DF0D2E2CB6"
        autofocus_id: "1C232674CD04024D"
        description: ["Real quest description"]
        rewards: [{ command: "/say The nether is disabled" }]
        tasks: [{
            item: {
                components: {
                    "ftbfiltersystem:filter": "or(mod(transfer_labels))"
                    "occultism:spirit_name": "Grimzirryne"
                    "minecraft:custom_name": "Sword of Testing"
                }
            }
        }]
    }]
}"""


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.sent = []

    def chat(self, messages, **kwargs):
        text = messages[-1]["content"]
        self.sent.append(text)
        return "ПЕР " + text


def make_translator(client, cache=None):
    cache = cache or TranslationCache(os.path.join(tempfile.mkdtemp(), "cache.json"))
    return Translator(client, cache, strict=True, log=lambda s: None, target_lang="ru_ru")


class ChapterFields(unittest.TestCase):
    def setUp(self):
        self.client = FakeLLM()
        self.output = translate_snbt_text_structured(CHAPTER, make_translator(self.client))

    def test_only_real_text_is_sent_to_the_model(self):
        self.assertEqual(
            sorted(self.client.sent),
            ["Real chapter title", "Real quest description", "Sword of Testing"],
        )

    def test_technical_values_survive_unchanged(self):
        for value in (
            "0D595252CB72400E", "129E0E2085D16CB5", "396CEB9D05700927", "196312DF0D2E2CB6",
            "1C232674CD04024D", "/say The nether is disabled", "or(mod(transfer_labels))",
            "Grimzirryne",
        ):
            self.assertIn(f'"{value}"', self.output)

    def test_real_text_is_translated(self):
        self.assertIn('"ПЕР Real quest description"', self.output)


class BareIdGuard(unittest.TestCase):
    """Запасной рубеж для путей вне структурного разбора (regex, JSON в lang)."""

    IDS = ["18E5446B92E771B8", "#5C040191740A53C8", "abcdefabcdefabcd"]

    def test_translate_returns_id_without_request_or_cache(self):
        client = FakeLLM()
        translator = make_translator(client)
        for value in self.IDS:
            self.assertEqual(translator.translate(value), value)
        self.assertEqual(client.sent, [])
        self.assertEqual(len(translator.cache), 0)

    def test_translate_many_returns_ids_without_request(self):
        client = FakeLLM()
        translator = make_translator(client)
        self.assertEqual(translator.translate_many(self.IDS), self.IDS)
        self.assertEqual(client.sent, [])
        self.assertEqual(len(translator.cache), 0)

    def test_old_cache_entry_cannot_rewrite_an_id(self):
        cache = TranslationCache(os.path.join(tempfile.mkdtemp(), "cache.json"))
        cache.put("18E5446B92E771B8", "Битый перевод", "ru_ru")
        translator = make_translator(FakeLLM(), cache)
        self.assertEqual(translator.translate("18E5446B92E771B8"), "18E5446B92E771B8")

    def test_similar_text_is_still_translated(self):
        client = FakeLLM()
        translator = make_translator(client)
        for text in ("Facade", "Decade of dead beef", "18E5446B92E771B"):
            translator.translate(text)
        self.assertEqual(len(client.sent), 3)


if __name__ == "__main__":
    unittest.main()
