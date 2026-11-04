"""Чистка кэша: удаляет только то, что не проходит текущие проверки, и всегда оставляет бэкап."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from tools.clean_mixed_cache import BACKUP_SUFFIX, clean_cache, rejection_reason

DATA = {
    "ru_ru": {
        "Minecraft": "Мinecraft",
        "Show culling boxes": "Показать кulling кубы",
        "Needs at least Netherite": "Необходимо至少 Netherite",
        "floor(x)\nRound x down": "пол(x)\nОкруглите x вниз",
        "Share %s now": "Поделиться сейчас",
        "§aGreen text": "Зелёный текст",
        "Strips codes": "Токены like [0] и [1] stand for markup. Оставьте каждый из них",
        "PKMNCC": "Покемонов не существует в Minecraft.",
        "Iron Ingot": "Железный слиток",
        "Pokémon team": "Команда Pokémon",
        "Share %s": "Поделиться %s",
        "floor(x)\nRound down": "floor(x)\nОкруглить вниз",
        "Sodium": "Sodium",
        "clamp(x, min, max)": "кламп(х, мин, макс)",
    },
    "de_de": {"Iron Ingot": "Eisenbarren"},
    "zh_cn": {"Needs more": "至少需要"},
}
BAD = {
    "Minecraft", "Show culling boxes", "Needs at least Netherite", "floor(x)\nRound x down",
    "Share %s now", "§aGreen text", "Strips codes", "PKMNCC",
}


class Reasons(unittest.TestCase):
    def test_each_kind_is_named(self):
        self.assertEqual(rejection_reason("Minecraft", "Мinecraft", "ru_ru"), "смесь алфавитов")
        self.assertEqual(rejection_reason("Needs more", "至少", "ru_ru"), "иероглифы")
        self.assertIn("разметка или код", rejection_reason("floor(x)\nRound", "пол(x)\nОкруглить", "ru_ru"))
        self.assertIn("floor(x)", rejection_reason("floor(x)\nRound", "пол(x)\nОкруглить", "ru_ru"))

    def test_prompt_leak_is_named(self):
        self.assertIn("текст промпта", rejection_reason("Strips codes", "Токены like и stand for markup", "ru_ru"))
        self.assertIsNone(rejection_reason("Token markup", "Токен markup", "ru_ru"))

    def test_invented_answer_is_named(self):
        self.assertIn("выдуманный", rejection_reason("PKMNCC", "Покемонов не существует в Minecraft.", "ru_ru"))
        self.assertIsNone(rejection_reason("Custom", "Персонализированный", "ru_ru"))

    def test_equal_to_source_is_not_a_reason(self):
        for text in ("Sodium", "Minecraft", "Share %s"):
            self.assertIsNone(rejection_reason(text, text, "ru_ru"))

    def test_clean_translation_passes(self):
        self.assertIsNone(rejection_reason("Share %s", "Поделиться %s", "ru_ru"))
        self.assertIsNone(rejection_reason("Pokémon team", "Команда Pokémon", "ru_ru"))

    def test_chinese_target_may_have_cjk(self):
        self.assertIsNone(rejection_reason("Needs more", "至少需要", "zh_cn"))

    def test_string_of_only_code_is_skipped_since_translator_never_reads_it(self):
        self.assertIsNone(rejection_reason("clamp(x, min, max)", "кламп(х, мин, макс)", "ru_ru"))


class CleanCache(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "cache.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(DATA, f, ensure_ascii=False)
        with open(self.path, "rb") as f:
            self.original = f.read()

    def _counts(self, stats):
        return stats["before"], stats["removed"], stats["after"]

    def test_dry_run_changes_nothing_and_lists_entries(self):
        stats = clean_cache(self.path)
        self.assertEqual(self._counts(stats), (16, 8, 8))
        self.assertEqual({e.source for e in stats["entries"]}, BAD)
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), self.original)
        self.assertFalse(os.path.exists(self.path + BACKUP_SUFFIX))

    def test_apply_removes_only_rejected_entries(self):
        self.assertEqual(self._counts(clean_cache(self.path, apply=True)), (16, 8, 8))
        with open(self.path, encoding="utf-8") as f:
            cleaned = json.load(f)
        for key, value in DATA["ru_ru"].items():
            if key in BAD:
                self.assertNotIn(key, cleaned["ru_ru"])
            else:
                self.assertEqual(cleaned["ru_ru"][key], value)
        self.assertEqual(cleaned["de_de"], DATA["de_de"])
        self.assertEqual(cleaned["zh_cn"], DATA["zh_cn"])

    def test_backup_is_byte_identical_to_the_original(self):
        clean_cache(self.path, apply=True)
        with open(self.path + BACKUP_SUFFIX, "rb") as f:
            self.assertEqual(f.read(), self.original)

    def test_existing_backup_is_not_overwritten(self):
        with open(self.path + BACKUP_SUFFIX, "w") as f:
            f.write("старый")
        with self.assertRaises(FileExistsError):
            clean_cache(self.path, apply=True)
        with open(self.path + BACKUP_SUFFIX) as f:
            self.assertEqual(f.read(), "старый")
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), self.original)

    def test_nothing_to_remove_makes_no_backup(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"ru_ru": {"Iron": "Железо"}}, f, ensure_ascii=False)
        self.assertEqual(clean_cache(self.path, apply=True)["removed"], 0)
        self.assertFalse(os.path.exists(self.path + BACKUP_SUFFIX))


if __name__ == "__main__":
    unittest.main()
