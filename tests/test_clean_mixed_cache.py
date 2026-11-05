"""Чистка кэша: удаляет только то, что не проходит текущие проверки, и всегда оставляет бэкап."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

from unittest import mock

from tools import clean_mixed_cache
from tools.clean_mixed_cache import BACKUP_SUFFIX, LISTED_REASON, clean_cache, rejection_reason, retranslate

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

    def test_preamble_and_raw_mask_are_named(self):
        for value in ("Хорошо, вот перевод: Диапазон", "Хорошо, вот перевод: [[Range]]"):
            self.assertIn("вступление", rejection_reason("Range", value, "ru_ru"))
        self.assertIsNone(rejection_reason("Of course", "Конечно", "ru_ru"))

    def test_foreign_latin_word_is_named(self):
        reason = rejection_reason("Dyes", "Цветные crayones", "ru_ru")
        self.assertIn("латинское слово", reason)
        self.assertIn("crayones", reason)
        for source, value in (("Pokémon team", "Команда Pokémon"), ("Screens", "На MacBook'ах"), ("Maps", "Карты (mipmapping)")):
            self.assertIsNone(rejection_reason(source, value, "ru_ru"))

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

    def test_listed_strings_are_removed_with_rejected_ones(self):
        stats = clean_cache(self.path, apply=True, also=["Iron Ingot", "PKMNCC"])
        self.assertEqual(self._counts(stats), (16, 9, 7))
        by_source = {(e.lang, e.source): e.reason for e in stats["entries"]}
        self.assertEqual(by_source[("ru_ru", "Iron Ingot")], LISTED_REASON)
        self.assertIn("выдуманный", by_source[("ru_ru", "PKMNCC")])
        with open(self.path, encoding="utf-8") as f:
            cleaned = json.load(f)
        self.assertNotIn("Iron Ingot", cleaned["ru_ru"])
        self.assertEqual(cleaned["de_de"], DATA["de_de"])

    def test_listed_string_missing_from_cache_changes_nothing(self):
        with self.assertRaises(KeyError):
            clean_cache(self.path, apply=True, also=["Нет такой строки"])
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), self.original)
        self.assertFalse(os.path.exists(self.path + BACKUP_SUFFIX))

    def test_nothing_to_remove_makes_no_backup(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"ru_ru": {"Iron": "Железо"}}, f, ensure_ascii=False)
        self.assertEqual(clean_cache(self.path, apply=True)["removed"], 0)
        self.assertFalse(os.path.exists(self.path + BACKUP_SUFFIX))


class Model:
    name = "strong"

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def chat(self, messages, **kwargs):
        text = messages[-1]["content"]
        self.calls.append(text)
        return self.answers[text]


class Retranslate(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "cache.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"ru_ru": {"Dyes": "Цветные crayones", "Bad one": "Плохо infliction", "Iron": "Железо"}}, f, ensure_ascii=False)

    def _cache(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)["ru_ru"]

    def test_answer_is_cached_and_defect_is_not(self):
        stats = clean_cache(self.path, apply=True)
        model = Model({"Dyes": "Красители", "Bad one": "Снова infliction"})
        results = retranslate(self.path, stats["entries"], model)
        self.assertEqual([(r.source, r.value, r.passed) for r in results], [
            ("Dyes", "Красители", True), ("Bad one", "Bad one", False),
        ])
        self.assertEqual(self._cache(), {"Iron": "Железо", "Dyes": "Красители"})
        self.assertEqual(set(model.calls), {"Dyes", "Bad one"})

    def test_cli_uses_only_the_given_model(self):
        model = Model({"Dyes": "Красители", "Bad one": "Плохая"})
        with mock.patch.object(clean_mixed_cache, "strong_ollama_client", return_value=model), \
                mock.patch("src.llm.factory.build_clients", side_effect=AssertionError("основная модель не нужна")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(clean_mixed_cache.main(["--cache", self.path, "--apply", "--retranslate"]), 0)
        self.assertEqual(self._cache(), {"Iron": "Железо", "Dyes": "Красители", "Bad one": "Плохая"})
        self.assertIn("Прошло все проверки: 2 из 2", out.getvalue())

    def test_no_strong_model_means_nothing_is_deleted(self):
        with open(self.path, "rb") as f:
            original = f.read()
        with mock.patch.object(clean_mixed_cache, "strong_ollama_client", side_effect=RuntimeError("нет")):
            with self.assertRaises(RuntimeError):
                clean_mixed_cache.main(["--cache", self.path, "--apply", "--retranslate"])
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), original)
        self.assertFalse(os.path.exists(self.path + BACKUP_SUFFIX))

    def test_fallback_to_a_local_model_is_refused(self):
        class Local:
            name = "qwen-standard"

        with mock.patch("src.llm.factory.build_strong_client", return_value=(Local(), "")):
            with self.assertRaises(RuntimeError):
                clean_mixed_cache.strong_ollama_client(object())

    def test_retranslate_needs_apply(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            clean_mixed_cache.main(["--cache", self.path, "--retranslate"])


if __name__ == "__main__":
    unittest.main()
