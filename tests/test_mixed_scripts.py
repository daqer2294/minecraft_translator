"""Слово из смеси кириллицы и латиницы: это брак модели, а не перевод."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

from src.translators import Translator
from src.utils.cache import TranslationCache
from src.utils.markup import foreign_latin_words, has_mixed_script_word

DAMAGED = [
    "Мinecraft", "Тwitch", "Лiberapay", "Вillage Butcher Chest", "Пink Plaque", "Хollow",
    "Мust survive explosion", "Резolution пикселей", "Сила пaddle", "Восксы иsplashы",
    "Показать кulling кубы", "Позицияchunk: %s", "СOLID", "Изменить сlідер", "Вигнитza",
    "Сhare Настройки С: %s", "§aМinecraft", "&6Тwitch&r", "Fluidы:", "Бees:", "нa",
    "Сила Водяного Пaddle", "создастwaypoint в указанном месте",
]

NORMAL = [
    "Железный слиток", "Pokémon атакует", "Minecraft запущен", "Уровень HP: %s",
    "%1$s нанёс урон %2$s", "{0} блоков", "§aЗелёный §rтекст", "&6Золотой &lжирный", "§lЖирный",
    "Первая строка\nВторая", "Первая\\nВторая", "X-образный", "Wi-Fi сеть", "кВт/ч", "HP/MP",
    "Minecraft'а", "Minecraft-а", "мод JEI", "CPU и GPU", "Версия 1.21.1", "3D-модель",
    "Mega-эволюция", "Нажмите <red>кнопку</red>", "$(item)Железо$() слиток",
    "Скачать: https://www.curseforge.com/minecraft", "minecraft:stone в печи", "Нажмите E",
    "x2 урона", "{@pagebreak}Следующая страница", "Plain English text", "%sшт", "10%", "Уровень II",
    "{image:atm:textures/x.png width:100}Смотри", "Chunk Позиция: %s", "",
]


class Detection(unittest.TestCase):
    def test_damaged_strings_are_found(self):
        for text in DAMAGED:
            self.assertTrue(has_mixed_script_word(text), text)

    def test_normal_strings_are_not_found(self):
        self.assertGreaterEqual(len(NORMAL), 20)
        for text in NORMAL:
            self.assertFalse(has_mixed_script_word(text), repr(text))

    def test_parts_joined_by_a_separator_are_checked_apart(self):
        for text in ("Android-приложение", "NPC/игрок", "API_ключ", "Mod'ы"):
            self.assertFalse(has_mixed_script_word(text), text)
        self.assertTrue(has_mixed_script_word("X-oбразный"), "латинская o внутри части")


class Scripted:
    name = "scripted"

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        user = messages[-1]["content"]
        if "JSON array" in messages[0]["content"]:
            return json.dumps([self.reply(x) for x in json.loads(user)], ensure_ascii=False)
        return self.reply(user)


def make_translator(client):
    tr = Translator(
        client, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
        strict=True, log=lambda s: None, target_lang="ru_ru",
    )
    tr.max_attempts = 1
    return tr


class Rejection(unittest.TestCase):
    def test_answer_rolls_back_and_is_not_cached(self):
        for answer in DAMAGED:
            tr = make_translator(Scripted(lambda t: answer))
            self.assertEqual(tr.translate("Some source text"), "Some source text", answer)
            self.assertEqual(len(tr.cache), 0, answer)

    def test_markup_stays_on_rollback(self):
        tr = make_translator(Scripted(lambda t: "Сhare Настройки С:"))
        self.assertEqual(tr.translate("§aShare Settings With: %s"), "§aShare Settings With: %s")

    def test_rejected_string_is_asked_again(self):
        client = Scripted(lambda t: "Мinecraft")
        tr = make_translator(client)
        tr.translate("Minecraft mod")
        tr.translate("Minecraft mod")
        self.assertEqual(client.calls, 2)

    def test_next_run_with_clean_answer_is_cached(self):
        tr = make_translator(Scripted(lambda t: "Мinecraft"))
        tr.translate("Minecraft mod")
        tr.client = Scripted(lambda t: "Мод Minecraft")
        self.assertEqual(tr.translate("Minecraft mod"), "Мод Minecraft")
        self.assertEqual(tr.cache.get("Minecraft mod", "ru_ru"), "Мод Minecraft")

    def test_batch_item_rolls_back_and_others_stay(self):
        tr = make_translator(Scripted(lambda t: "Мinecraft" if t == "Bad item" else "ПЕР " + t))
        with contextlib.redirect_stdout(io.StringIO()):
            out = tr.translate_many(["Bad item", "Good item"])
        self.assertEqual(out, ["Bad item", "ПЕР Good item"])
        self.assertIsNone(tr.cache.get("Bad item", "ru_ru"))
        self.assertEqual(tr.cache.get("Good item", "ru_ru"), "ПЕР Good item")

    def test_works_with_validation_off(self):
        tr = make_translator(Scripted(lambda t: "Мinecraft"))
        tr.strict = False
        self.assertEqual(tr.translate("Minecraft mod"), "Minecraft mod")

    def test_normal_answers_pass_and_are_cached(self):
        for answer in NORMAL:
            if not answer or not answer.strip():
                continue
            tr = make_translator(Scripted(lambda t: answer))
            tr.strict = False  # здесь проверяется только смесь алфавитов, а не совпадение разметки
            # Слова ответа есть в источнике: придуманная латиница проверяется в test_foreign_latin_words.
            source = " ".join(["Some source text", *foreign_latin_words("", answer)])
            self.assertEqual(tr.translate(source), answer.strip())


if __name__ == "__main__":
    unittest.main()
