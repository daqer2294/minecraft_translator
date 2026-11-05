"""Модель пересказывает наш промпт вместо перевода: это брак, даже если разметка цела."""
from __future__ import annotations

import os
import tempfile
import unittest

from src.translators import (
    BATCH_SYSTEM_PROMPT_TEMPLATE,
    PROMPT_LEAK,
    PROMPT_TEXTS,
    SINGLE_SYSTEM_PROMPT_TEMPLATE,
    Translator,
)
from src.utils.cache import TranslationCache
from src.utils.markup import MASK_PROMPT_NOTE, MASK_PROMPT_NOTE_RU, foreign_latin_words
from src.utils.prompt_leak import PromptLeakDetector

REAL_LEAKS = [
    ("Strips Formatting Codes from the inputted String",
     "Токены like [0] и [1] stand for markup. Оставьте каждый из них в точном количестве и без комментариев в своем первоначальном положении."),
    ("Blocks spawned\nsets the texture for the entity based on the block it spawned on",
     "Токены like \n и \n stand для маркетинг. Оставьте каждый из них в точном виде, без изменений, в своем естественном положении."),
]

RULE_ECHOES = [
    ("Edit as text", "Do NOT translate or alter placeholders"),
    ("Done", "Return ONLY the translated text, without quotes or explanations."),
    ("Done", "You are a professional localization engine"),
    ("Keep it", "Оставьте каждый из них ровно один раз"),
]

# источник -> законный перевод
NORMAL = [
    ("Iron Ingot", "Железный слиток"),
    ("Pokémon team", "Команда Pokémon"),
    ("Exit Minecraft", "Выход из Minecraft"),
    ("Game config file", "Файл JSON конфигурации Minecraft"),
    ("Mod list", "Список модов Mods"),
    ("Item ID", "ID предмета"),
    ("Each of them gets a reward", "Каждый из них получает награду"),
    ("Keep every one of them", "Сохрани всех"),
    ("Token", "Токен"),
    ("Token count: %s", "Количество токенов: %s"),
    ("Placeholder text", "Текст-заполнитель"),
    ("Use a placeholder here", "Используйте здесь placeholder"),
    ("The markup language", "Язык разметки markup"),
    ("Markup and tokens are kept", "Разметка markup и tokens сохраняются"),
    ("Token like stand for markup", "Токен like stand for markup"),
    ("Tokens like this stand for markup", "Tokens like this stand for markup здесь"),
    ("Translate the following text into Russian", "Translate the following text на русский"),
    ("Return only the translated text", "Return only the translated text на экран"),
    ("Do not translate this", "Do not translate это"),
    ("Hard rules apply", "Действуют жёсткие правила"),
    ("Without quotes", "Без кавычек"),
    ("Natural position of the block", "Естественное положение блока"),
    ("Copy this block", "Скопировать этот блок"),
    ("Stand on the block", "Встань на блок"),
    ("Keep the items", "Сохранить предметы"),
    ("Unchanged", "Без изменений"),
    ("Exactly once", "Ровно один раз"),
    ("Windows only", "Только Windows"),
    ("Press Shift for more", "Нажмите Shift для подробностей"),
    ("Uses the OptiFine format", "Использует формат OptiFine"),
    ("CPU and GPU", "CPU и GPU"),
    ("%s of %s", "%s из %s"),
]


class Detector(unittest.TestCase):
    def test_real_leaks_are_found(self):
        for source, answer in REAL_LEAKS:
            self.assertTrue(PROMPT_LEAK.explain(answer, source), answer)

    def test_echo_of_the_rules_is_found(self):
        for source, answer in RULE_ECHOES:
            self.assertTrue(PROMPT_LEAK.explain(answer, source), answer)

    def test_normal_strings_pass(self):
        self.assertGreaterEqual(len(NORMAL), 30)
        for source, answer in NORMAL:
            self.assertEqual(PROMPT_LEAK.explain(answer, source), "", (source, answer))

    def test_words_from_the_source_do_not_count(self):
        self.assertEqual(PROMPT_LEAK.explain("Tokens stand здесь", "Tokens stand here"), "")
        self.assertTrue(PROMPT_LEAK.explain("Tokens stand здесь", "Something else"))

    def test_scattered_words_count_only_in_cyrillic_answers(self):
        self.assertEqual(PROMPT_LEAK.explain("Der Block stand like früher", "The block"), "")

    def test_english_phrase_is_found_in_any_language(self):
        self.assertTrue(PROMPT_LEAK.explain("Hinweis: stand for markup", "A note"))


class SharedSource(unittest.TestCase):
    def test_every_prompt_is_in_the_set(self):
        for text in (SINGLE_SYSTEM_PROMPT_TEMPLATE, BATCH_SYSTEM_PROMPT_TEMPLATE, MASK_PROMPT_NOTE, MASK_PROMPT_NOTE_RU):
            self.assertIn(text, PROMPT_TEXTS)

    def test_changing_a_prompt_changes_the_check(self):
        detector = PromptLeakDetector(["Purple elephants always dance quietly tonight."])
        self.assertTrue(detector.explain("Сегодня purple elephants always танцуют", "Dance"))
        self.assertEqual(detector.explain("Токены like и stand for markup", "Dance"), "")

    def test_template_fields_are_not_prompt_words(self):
        detector = PromptLeakDetector(["Translate into __LANG_NAME__ (locale: __LANG_CODE__)."])
        self.assertEqual(detector.explain("Имя lang и name", "Lang name"), "")


class Model:
    name = "model"

    def __init__(self, reply):
        self.reply, self.sent = reply, []

    def chat(self, messages, **kwargs):
        self.sent.append(messages[-1]["content"])
        return self.reply(messages[-1]["content"])


def make_translator(reply):
    tr = Translator(Model(reply), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                    log=lambda s: None, target_lang="ru_ru")
    tr.max_attempts = 1
    return tr


class Translation(unittest.TestCase):
    def test_leak_rolls_back_is_not_cached_and_goes_to_second_pass(self):
        source, answer = REAL_LEAKS[0]
        tr = make_translator(lambda t: answer)
        self.assertEqual(tr.translate(source), source)
        self.assertIsNone(tr.cache.get(source, "ru_ru"))
        self.assertEqual(tr.take_rejected(), [(source, "ru_ru")])

    def test_leak_with_all_tokens_in_place_is_rejected(self):
        source = "Blocks spawned\nsets the texture\nfor the entity"
        tr = make_translator(lambda t: "Токены like [[0]] и [[1]] stand для маркетинг. Оставьте каждый из них")
        self.assertEqual(tr.translate(source), source)
        self.assertIsNone(tr.cache.get(source, "ru_ru"))

    def test_batch_item_with_leak_is_rolled_back(self):
        import contextlib
        import io
        import json

        class Batch(Model):
            def chat(self, messages, **kwargs):
                user = messages[-1]["content"]
                if "JSON array" in messages[0]["content"]:
                    return json.dumps([REAL_LEAKS[0][1] if x == "Bad item" else "ПЕР " + x for x in json.loads(user)], ensure_ascii=False)
                return "ПЕР " + user

        tr = Translator(Batch(None), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang="ru_ru")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tr.translate_many(["Bad item", "Good item"]), ["Bad item", "ПЕР Good item"])
        self.assertIsNone(tr.cache.get("Bad item", "ru_ru"))

    def test_normal_translation_with_prompt_words_in_source_is_kept(self):
        for source, answer in NORMAL:
            # Слово, которого нет в источнике, бракует другой валидатор: здесь проверяется только утечка.
            if "%" in source or foreign_latin_words(source, answer):
                continue
            tr = make_translator(lambda t: answer)
            self.assertEqual(tr.translate(source), answer, source)


if __name__ == "__main__":
    unittest.main()
