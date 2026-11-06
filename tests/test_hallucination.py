"""Короткий источник и длинный ответ о другом: модель придумала текст, а не перевела."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

from src.translators import Translator
from src.utils.cache import TranslationCache
from src.utils.markup import looks_hallucinated

INVENTED = [
    ("PKMNCC", "Покемонов не существует в Minecraft."),
    ("Only splashes", "Смелость и храбрость не всегда помогают в этой игре."),
    ("Cheat Items to Hotbar Using Hotkeys",
     'Нажмите на кнопку "Настройки" в меню и включите функцию "Скорые доступы к предметам". '
     'Затем нажмите на кнопку "Горячие клавиши" и выберите нужные клавиши для быстрого доступа к предметам.'),
]

# A5-A7 из списка и обычные короткие строки
NORMAL = [
    ("Edit", "Редактирование"),
    ("Bees:", "Медоносные пчелы:"),
    ("Custom", "Персонализированный"),
    ("Iron Ingot", "Железный слиток"),
    ("Save", "Сохранить"),
    ("Sneak", "Присесть на корточки"),
    ("Hotbar", "Панель быстрого доступа"),
    ("FPS", "Кадров в секунду"),
    ("HUD", "Индикаторы на экране игрока"),
    ("Melee Range", "Ранговая дистанция ближнего боя"),
    ("Open Collective", "Открытая Коллективная Социальная Система"),
    ("Gilded Chests", "Золотые Кованые Кованые Крышки"),
    ("Arrows heal nearby players.", "Летающие предметы восстанавливают здоровье ближайших игроков."),
    ("Smooth Scroll Rate", "Способность плавного скроллинга скорости"),
    ("Eat Grass Chance", "Ешь шанс на съедение растительности"),
    ("Village - Tannery", "Торговая деревня - Ткацкая мастерская"),
    ("Flee Desired Distance", "Убеждайтесь в удалении от желаемой дистанции"),
    ("Maximum zoom FOV.", "Максимальный угол обзора для зумма."),
    ("Configure Hot Reload", "Настроить перезагрузку в реальном времени"),
    ("Max number of columns shown.", "Максимальное количество столбцов, которые будут показаны."),
    ("Done", "Готово"),
    ("Cancel", "Отмена"),
    ("Back to game", "Вернуться в игру"),
    ("Reload", "Перезагрузить"),
]


class Detection(unittest.TestCase):
    def test_invented_answers_are_found(self):
        for source, answer in INVENTED:
            self.assertTrue(looks_hallucinated(source, answer), source)

    def test_normal_short_strings_pass(self):
        self.assertGreaterEqual(len(NORMAL), 23)
        for source, answer in NORMAL:
            self.assertFalse(looks_hallucinated(source, answer), source)

    def test_every_condition_is_needed(self):
        long_answer = "раз два три четыре пять шесть семь восемь девять десять"
        self.assertTrue(looks_hallucinated("One two", long_answer))
        self.assertFalse(looks_hallucinated("One two three four five six seven", long_answer), "источник длиннее 6 слов")
        self.assertFalse(looks_hallucinated("One", "Оченьоченьдлинноеслово и ещё"), "в ответе меньше 5 слов")
        self.assertFalse(looks_hallucinated("A source of fair size here", "Раз два три четыре пять шесть"), "не длиннее в 3 раза")

    def test_exactly_three_times_longer_is_not_enough(self):
        self.assertFalse(looks_hallucinated("x" * 10, "а б в г " + "д" * 22))
        self.assertTrue(looks_hallucinated("x" * 10, "а б в г " + "д" * 23))

    def test_source_with_markup_or_code_is_skipped(self):
        long_answer = "Это очень длинный ответ совсем о другом и ещё о чём-то"
        for source in ("Hello %s", "§aDone", "clamp(x, y)", "fooBar here", "One\nTwo"):
            self.assertFalse(looks_hallucinated(source, long_answer), source)


class Model:
    name = "model"

    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, **kwargs):
        user = messages[-1]["content"]
        if "JSON array" in messages[0]["content"]:
            return json.dumps([self.reply(x) for x in json.loads(user)], ensure_ascii=False)
        return self.reply(user)


def make_translator(reply):
    tr = Translator(Model(reply), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                    log=lambda s: None, target_lang="ru_ru")
    tr.max_attempts = 1
    return tr


class Translation(unittest.TestCase):
    def test_rolls_back_is_not_cached_and_goes_to_second_pass(self):
        for source, answer in INVENTED:
            tr = make_translator(lambda t: answer)
            self.assertEqual(tr.translate(source), source)
            self.assertIsNone(tr.cache.get(source, "ru_ru"))
            self.assertEqual(tr.take_rejected(), [(source, "ru_ru")])

    def test_works_with_validation_off(self):
        tr = make_translator(lambda t: INVENTED[0][1])
        tr.strict = False
        self.assertEqual(tr.translate("PKMNCC"), "PKMNCC")

    def test_batch_item_is_rolled_back(self):
        tr = make_translator(lambda t: INVENTED[0][1] if t == "PKMNCC" else "ПЕР " + t)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tr.translate_many(["PKMNCC", "Good item"]), ["PKMNCC", "ПЕР Good item"])
        self.assertIsNone(tr.cache.get("PKMNCC", "ru_ru"))

    def test_normal_answers_are_kept_and_cached(self):
        for source, answer in NORMAL:
            tr = make_translator(lambda t: answer)
            self.assertEqual(tr.translate(source), answer, source)
            self.assertEqual(tr.cache.get(source, "ru_ru"), answer)


if __name__ == "__main__":
    unittest.main()
