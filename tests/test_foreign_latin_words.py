"""Английское слово в русском ответе, которого нет в источнике: это брак. Пары взяты из настоящего кэша."""
from __future__ import annotations

import os
import tempfile
import unittest

from src.translators import Translator
from src.utils.cache import TranslationCache
from src.utils.markup import foreign_latin_words

BAD = [
    ("Config options related to Cheating.", "Опции конфига связанные с Cheat.", ["Cheat"]),
    ("Deepslate Cobblestones", "Глубокое Slate Камень", ["Slate"]),
    ("Your team's Lucky Chant wore off!", "Ваша команда's Fortuity выветрилась!", ["Fortuity"]),
    (
        "The user unleashes its grudge on the move last used by the target by cutting 4 PP from it.",
        "Пользователь unleash его злость на последнее место, которое использовало цель, нанося ей урон в 4 PP.",
        ["unleash"],
    ),
    (
        "Being hit by projectiles sometimes produces arrows.",
        "Быть пораженным Projectile иногда производит Arrows.",
        ["Projectile"],
    ),
    ("Display Entities as Dots", "Показывать entidad как точки", ["entidad"]),
    ("Surface", "Сер face", ["face"]),
    ("Date format for death waypoints.", "Дата формата для_waypoint_мира_смерти", ["waypoint"]),
    ("Increases healing received by 20% each level.", "Увеличивает получаемую Heal на 20% каждый уровень.", ["Heal"]),
    ("Dyes", "Цветные crayones", ["crayones"]),
]

# Те самые три случая, ради которых правило сужено до варианта C.
MIPMAPPING = (
    "Controls the number of mipmaps which will be used for block model textures. Higher values provide better "
    "rendering of blocks in the distance, but could adversely affect performance with resource packs that use "
    "many animated textures.",
    "Управляет количеством текстурных карт (mipmapping), используемых для текстур блоков. Высокие значения "
    "обеспечивают лучшее отображение блоков на расстоянии, но могут негативно сказаться на производительности "
    "при ресурсных паках, использующих много анимированных текстур.",
)
PLACEHOLDERS = (
    "Whether to use this module as the main RPC data\\n(If disabled, this data will only be used for replacing "
    "related message and icon placeholders)",
    "Что использовать этот мод как основной источник RPC данных\\n(Если выключить, то эти данные будут "
    "использоваться только для замены placeholder'ов, связанных с сообщениями и иконками)",
)
MACBOOK = (
    "Custom drag scale, higher number slows it down. This is useful when your OS does display scaling, "
    "primarily OSX retina displays.",
    "Кастомная регулировка скорости перетаскивания, больший номер замедляет ее. Это полезно при использовании "
    "операционной системы с нестандартным масштабированием экрана, в частности на MacBook'ах с Retina-экранами.",
)

NORMAL = [
    MIPMAPPING,
    PLACEHOLDERS,
    MACBOOK,
    ("Pokémon team", "Команда Pokémon"),
    ("POKÉMON team", "Команда Pokémon"),
    ("Welcome to Minecraft", "Добро пожаловать в Minecraft"),
    ("Enter the URL", "Введите URL"),
    ("Uses 100 mB of water", "Использует 100 mB воды"),
    ("Restores 5 HP", "Восстанавливает 5 HP"),
    ("Iron Ingot", "Железный слиток"),
    ("Share %s", "Поделиться %s"),
    ("Uses floor(x)", "Использует floor(x)"),
    ("Press §aStart", "Нажмите §aStart"),
    # Ответ без кириллицы: правило для русского ответа. Немецкий перевод и строка как есть не страдают.
    ("Iron Ingot", "Eisenbarren"),
    ("Sodium", "Sodium"),
]


class Detection(unittest.TestCase):
    def test_real_defects_name_the_word(self):
        self.assertEqual(len(BAD), 10)
        for source, answer, words in BAD:
            self.assertEqual(foreign_latin_words(source, answer), words, source)

    def test_real_terms_written_in_russian_are_not_defects(self):
        for source, answer in (MIPMAPPING, PLACEHOLDERS, MACBOOK):
            self.assertEqual(foreign_latin_words(source, answer), [], source)

    def test_normal_strings_pass(self):
        for source, answer in NORMAL:
            self.assertEqual(foreign_latin_words(source, answer), [], (source, answer))

    def test_russian_ending_with_typographic_apostrophe(self):
        self.assertEqual(foreign_latin_words("Displays", "На MacBook’ах"), [])

    def test_exceptions_need_the_full_form(self):
        self.assertEqual(foreign_latin_words("Some text", "Текст (mipmapping здесь)"), ["mipmapping"])
        self.assertEqual(foreign_latin_words("Some text", "Текст placeholder's"), ["placeholder"])

    def test_short_words_do_not_count(self):
        self.assertEqual(foreign_latin_words("Some", "Текст abc"), [])
        self.assertEqual(foreign_latin_words("Some", "Текст abcd"), ["abcd"])


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

    def test_defect_rolls_back_is_not_cached_and_goes_to_second_pass(self):
        tr = self._translator("Цветные crayones")
        self.assertEqual(tr.translate("Dyes"), "Dyes")
        self.assertIsNone(tr.cache.get("Dyes", "ru_ru"))
        self.assertEqual(tr.take_rejected(), [("Dyes", "ru_ru")])

    def test_validation_off_still_rejects(self):
        tr = self._translator("Цветные crayones")
        tr.strict = False
        self.assertEqual(tr.translate("Dyes"), "Dyes")

    def test_term_written_in_russian_is_kept(self):
        tr = self._translator("На MacBook'ах")
        self.assertEqual(tr.translate("On laptops"), "На MacBook'ах")


if __name__ == "__main__":
    unittest.main()
