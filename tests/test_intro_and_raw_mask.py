"""Вступление вместо перевода и нераскрытая маска: это брак, даже если перевод рядом есть."""
from __future__ import annotations

import os
import tempfile
import unittest

from src.translators import Translator
from src.utils.cache import TranslationCache
from src.utils.markup import has_intro_or_raw_mask

BAD = [
    ("Horizontal Wander Range", "Хорошо, вот перевод: [[Horizontal Wander Range]]"),
    ("Horizontal Wander Range", "Хорошо, вот перевод: Горизонтальный диапазон"),
    ("Iron Ingot", "Вот перевод: Железный слиток"),
    ("Iron Ingot", "Вот ваш перевод: Железный слиток"),
    ("Iron Ingot", "Конечно, Железный слиток"),
    ("Iron Ingot", "Конечно! Железный слиток"),
    ("Iron Ingot", "Перевод: Железный слиток"),
    ("Iron Ingot", "Переведено: Железный слиток"),
    ("Iron Ingot", "Here is the translation: Железный слиток"),
    ("Iron Ingot", "Here's the translation: Железный слиток"),
    ("Iron Ingot", "Sure, Железный слиток"),
    ("Iron Ingot", "Sure! Железный слиток"),
    ("Iron Ingot", "Translation: Железный слиток"),
    ("Iron Ingot", "Translated: Железный слиток"),
    ("Iron Ingot", "«Хорошо, вот перевод»: Железный слиток"),
    ("Iron Ingot", "Железный [[0]] слиток"),
    ("Iron Ingot", "[[Iron Ingot]]"),
    ("Iron Ingot", "Железный слиток [[ 1 ]]"),
]

NORMAL = [
    ("Good", "Хорошо"),
    ("Good, that works", "Хорошо, это работает"),
    ("Very good", "Очень хорошо"),
    ("Of course", "Конечно"),
    ("Of course, it works", "Конечно, это работает"),
    ("Sure", "Конечно"),
    ("Certainly, my friend", "Конечно, мой друг"),
    ("Sure thing", "Sure thing"),
    ("Here it is", "Вот он"),
    ("That's all", "Вот и всё"),
    ("Here is your reward", "Вот ваша награда"),
    ("Here is your reward", "Here is your reward"),
    ("Translation", "Перевод"),
    ("Translation: %s", "Перевод: %s"),
    ("Translated: %s", "Переведено: %s"),
    ("Translate text", "Перевести текст"),
    ("Please edit the sign again", "Пожалуйста, редактируй знак снова"),
    ("Warning: Occurred once", "Предупреждение: Произошло один раз"),
    ("Mouse Location", "Мышь: Локация"),
    ("Village Butcher Chest", "Сельский Мясник: Сундук"),
    ("Iron Ingot", "Железный слиток"),
    ("Hello %s", "Привет %s"),
    ("[Optional] text", "[Необязательно] текст"),
    ("[[Wiki link]] here", "[[Wiki link]] здесь"),
    ("Use [[Wiki link]] and more", "Используй [[Wiki link]] и другое"),
    ("Array [1] item", "Массив [1] элемент"),
    ("Press [E]", "Нажмите [E]"),
    ("Sure Shot", "Верный выстрел"),
    ("Conclusion", "Конечное решение"),
    ("Translator mode", "Режим переводчика"),
    ("Item list", "Вот список предметов"),
    ("Here", "Вот"),
]


class Detection(unittest.TestCase):
    def test_intro_and_raw_mask_are_found(self):
        for source, answer in BAD:
            self.assertTrue(has_intro_or_raw_mask(source, answer), (source, answer))

    def test_normal_strings_pass(self):
        self.assertGreaterEqual(len(NORMAL), 30)
        for source, answer in NORMAL:
            self.assertFalse(has_intro_or_raw_mask(source, answer), (source, answer))


class Translation(unittest.TestCase):
    class Model:
        name = "m"

        def __init__(self, answer):
            self.answer = answer

        def chat(self, messages, **kwargs):
            return self.answer

    def _translator(self, answer):
        tr = Translator(self.Model(answer), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang="ru_ru")
        tr.max_attempts = 1
        return tr

    def test_real_case_rolls_back_is_not_cached_and_goes_to_second_pass(self):
        source, answer = BAD[0]
        tr = self._translator(answer)
        self.assertEqual(tr.translate(source), source)
        self.assertIsNone(tr.cache.get(source, "ru_ru"))
        self.assertEqual(tr.take_rejected(), [(source, "ru_ru")])

    def test_validation_off_still_rejects(self):
        source, answer = BAD[0]
        tr = self._translator(answer)
        tr.strict = False
        self.assertEqual(tr.translate(source), source)

    def test_clean_answer_is_kept(self):
        tr = self._translator("Хорошо")
        self.assertEqual(tr.translate("Good"), "Хорошо")


if __name__ == "__main__":
    unittest.main()
