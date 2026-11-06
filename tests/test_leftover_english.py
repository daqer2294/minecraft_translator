"""Английское слово из источника внутри русского текста. Пары BAD из настоящего кэша, дословно."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from src import translators
from src.translators import Translator
from src.utils.cache import TranslationCache
from src.utils.markup import leftover_english_words
from tools.clean_mixed_cache import rejection_reason

BAD = [
    ("While it is young, it uses the nutrients that are stored in the seed on its back in order to grow.",
     "Во время молодости оно использует питательные вещества,stored на его спине в виде семени, для роста.",
     ["stored"]),
    ("The target is licked with a long tongue, causing damage. This may also leave the target with paralysis.",
     "Т target licked with a long tongue, causing damage. This may also leave the target with paralysis.",
     ["target", "licked", "with", "long", "tongue", "causing", "damage", "also", "leave", "target", "with", "paralysis"]),
    ("The user ensnares the target with thin, gooey silk so it can't flee from battle.",
     "Пользователь ensnares цели в тонкой, густой сите, так что они не могут убежать из боя.",
     ["ensnares"]),
    ("The user growls in an endearing way, making opposing Pokémon less wary.",
     "Пользователь growls в умильном тоне, что делает противников менее осторожными.", ["growls"]),
    ("Spawn weather particles above cloud height", "Spawn weather particles выше высоты облаков",
     ["weather", "particles"]),
    ("Sub-model texture handling", "Т texture обработки субмоделей", ["texture"]),
    ("Animation math details", "Математика анимации details", ["details"]),
    ("Show recipes for ingredient tags like item tags and block tags.",
     "Показать рецепты для тегов ингредиентов like тегов предметов и блоков.", ["like"]),
    ("A sticky web has been laid on the ground around your team!",
     "С sticky сетью охвачена команда вашего отряда на земле!", ["sticky"]),
    ("%1$s absorbed electricity!", "%1$s absorbed электричество!", ["absorbed"]),
    ("Pauses idle activity when running action effects.", "Пауза в idle активности при выполнении действий.",
     ["idle"]),
    ("Subfolder models MUST variate", "Подпапка models МНЕСТВЕННО должна варьироваться", ["models"]),
]

NORMAL = [
    ("easeinback(k, x, y) using the ease in back function", "функции ease in back"),
    ("Only the original vanilla model parts", "Только оригинальные модели vanilla"),
    ("Set to true or false", "Установите true или false"),
    ("Types: both, world, server", "Типы: both, world, server"),
    ("Value <normal|lazy|worried|playful>", "Значение <normal|lazy|worried|playful>"),
    ("Match name=regex", "Совпадает name=regex"),
    ("Hour of the day §ahour.<n>=<list>", "Час суток §ahour.<n>=<list>"),
    ("Baby Only valid for mobs §ababy.<n>=<true/false>", "Малыш Только для мобов §ababy.<n>=<true/false>"),
    ("Use the file options.txt here", "Используйте файл options.txt"),
    ("Open https://example.com/docs/page now", "Откройте https://example.com/docs/page"),
    ("The word \"target\" is here", "Здесь слово \"target\""),
    ("The word «target» is here", "Здесь слово «target»"),
    ("The field 'target' is here", "Здесь поле 'target'"),
    ("Press `shift` now", "Нажмите `shift`"),
    ("The Pokémon Bulbasaur sleeps", "Покемон Bulbasaur спит"),
    ("Mod Menu is open", "Открыт Mod Menu"),
    ("Uses floor(x) value", "Использует floor(x)"),
    ("Deals camelCase damage", "Наносит camelCase урон"),
    ("Deals snake_case damage", "Наносит snake_case урон"),
    ("Use 100 mB", "Используйте 100 mB"),
    ("Uses json data", "Использует данные json"),
    ("Word not in source", "Слово unknown здесь"),
    ("Short words stay", "Слова and or оставить"),
    ("Hello there", "Привет"),
    ("Fights Melee", "Бои в ближнем бою"),
    ("Iron Ingot", "Железный слиток"),
    ("Press §aStart", "Нажмите §aStart"),
]
NORMAL_WITH_ALLOWED_WORD_OUT = ("Uses json data", "Использует данные json", ["json"])


class Rule(unittest.TestCase):
    def test_real_defects_name_the_words(self):
        for source, answer, words in BAD:
            self.assertEqual(leftover_english_words(source, answer, "ru_ru"), words, answer)

    def test_normal_answers_pass(self):
        for source, answer in NORMAL:
            if (source, answer) == NORMAL_WITH_ALLOWED_WORD_OUT[:2]:
                continue
            self.assertEqual(leftover_english_words(source, answer, "ru_ru"), [], answer)

    def test_plain_word_from_the_source_still_counts_even_if_technical(self):
        source, answer, words = NORMAL_WITH_ALLOWED_WORD_OUT
        self.assertEqual(leftover_english_words(source, answer, "ru_ru"), words)

    def test_only_cyrillic_targets_and_answers(self):
        source, answer = "Spawn weather particles", "Spawn weather particles выше"
        self.assertEqual(leftover_english_words(source, answer, "de_de"), [])
        self.assertEqual(leftover_english_words(source, "weather particles", "ru_ru"), [])

    def test_short_list_is_not_a_value_list(self):
        self.assertEqual(leftover_english_words("Sticky, shiny web", "Паутина sticky, shiny", "ru_ru"), ["sticky", "shiny"])

    def test_cleanup_names_the_reason(self):
        self.assertIn("английское слово", rejection_reason(BAD[2][0], BAD[2][1], "ru_ru"))
        self.assertIsNone(rejection_reason("Set to true or false", "Установите true или false", "ru_ru"))


class Model:
    name = "m"

    def __init__(self, answer):
        self.answer = answer

    def chat(self, messages, **kwargs):
        return self.answer


class Translation(unittest.TestCase):
    """В пакете тестов проверка отключена для заглушек, здесь она настоящая."""

    def setUp(self):
        patcher = mock.patch.object(translators, "leftover_english_words", leftover_english_words)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _translator(self, answer):
        tr = Translator(Model(answer), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang="ru_ru")
        tr.max_attempts = 1
        return tr

    def test_short_text_rolls_back_and_goes_to_second_pass(self):
        source = "Spawn weather particles above"
        self.assertLess(len(source.split()), 15)
        tr = self._translator("Spawn weather particles выше")
        self.assertEqual(tr.translate(source), source)
        self.assertIsNone(tr.cache.get(source, "ru_ru"))
        self.assertEqual(tr.take_rejected(), [(source, "ru_ru")])

    def test_validation_off_still_rejects(self):
        source = "Animation math details"
        tr = self._translator("Математика анимации details")
        tr.strict = False
        self.assertEqual(tr.translate(source), source)

    def test_clean_answer_is_kept(self):
        tr = self._translator("Частицы погоды")
        self.assertEqual(tr.translate("Spawn weather particles"), "Частицы погоды")


if __name__ == "__main__":
    unittest.main()
