"""Тег в ответе, которого нет в источнике: «ease вы[/ru]». RU_TAG и ENDING_TAG из настоящего кэша дословно."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

from src.translators import Translator
from src.utils.cache import TranslationCache
from src.utils.markup import invented_tags

RU_TAG = (
    "easeinoutbounce(k, x, y)\nInterpolates between x and y using the ease in and ease out bounce function\n\n"
    "examples here: https://easings.net/",
    "easeinoutbounce(k, x, y)\nПерелинейный интерполятор между x и y с использованием функции ease в и ease вы[/ru]\n\n"
    "примеры здесь: https://easings.net/",
)
ENDING_TAG = (
    "Affected entities will slowly slide forward when on the ground, and each tick have a 1/300 chance (roughly 6.5% "
    "chance each second) of dropping either their held item or their offhand item",
    "Зависимые сущности медленно скользят вперёд, когда они на земле, и каждую тик[е] (приблизительно 6,5% шанса в "
    "секунду) имеют шанс выпустить то, что они держат, или то, что находится в их втором руке.",
)

BAD = [
    (*RU_TAG, ["[/ru]"]),
    (*ENDING_TAG, ["[е]"]),
    ("Iron Ingot", "Железный слиток[ru]", ["[ru]"]),
    ("Iron Ingot", "Железный слиток<ru>", ["<ru>"]),
    ("Iron Ingot", "<translation>Железный слиток</translation>", ["<translation>", "</translation>"]),
    ("Iron Ingot", "Железный<br/>слиток", ["<br/>"]),
    ("Iron Ingot", "[color=red]Железный слиток[/color]", ["[color=red]", "[/color]"]),
    # Закрывающий тег и угловые скобки не переводятся, даже если подпись из источника пропала.
    ("Hold [Shift]", "Удерживайте [/Шифт]", ["[/Шифт]"]),
    ("Set <name>", "Задать <имя>", ["<имя>"]),
    # Латинский тег в русском ответе это не перевод подписи.
    ("Hold [Shift]", "Удерживайте [ru]", ["[ru]"]),
    # Подпись пропала одна, а новых тегов два.
    ("Hold [Shift]", "Удерживайте [Шифт] и [Альт]", ["[Альт]"]),
]

# Источник и ответ, в которых ничего не выдумано: 25 обычных строк и настоящие переводы подписей.
NORMAL = [
    ("Hold [Shift] to sneak", "Удерживайте [Shift], чтобы красться"),
    ("[Shift] + Click", "[Shift] + клик"),
    ("[Left Click] to open", "[Left Click], чтобы открыть"),
    ("[Right Click] to place", "[Right Click], чтобы поставить"),
    ("Press [Left Click] or [Right Click]", "Нажмите [Left Click] или [Right Click]"),
    ("[Left Click] to open", "[Левый клик], чтобы открыть"),
    ("Set <name>", "Задать <name>"),
    ("Usage: /give <player> <item>", "Использование: /give <player> <item>"),
    ("<red>Warning</red>", "<red>Внимание</red>"),
    ("Line one<br>Line two", "Первая строка<br>Вторая строка"),
    ("<searchTerm|type:name> to search", "<searchTerm|type:name> для поиска"),
    ("Enter <true|false>", "Введите <true|false>"),
    ("[SHIFT] to sneak", "[shift], чтобы красться"),
    ("§aHold §l[Shift]", "§aУдерживайте §l[Shift]"),
    ("§6Gold §rtext &aGreen", "§6Золотой §rтекст &aЗелёный"),
    ("Value [[0]] of [[1]]", "Значение [[0]] из [[1]]"),
    ("Deals %s damage", "Наносит %s урона"),
    ("{0} of {count}", "{0} из {count}"),
    ("Line\\nNext", "Строка\\nСледующая"),
    ("Scale [1.5]", "Масштаб [1.5]"),
    ("Position [%d, %d, %d]", "Позиция [%d, %d, %d]"),
    ("Iron (Ingot)", "Железо (слиток)"),
    ("a < b and c > d", "а < б и в > г"),
    ("1/2 and/or 3/4", "1/2 и/или 3/4"),
    ("[Docs](https://example.com)", "[Документация](https://example.com)"),
]
TRANSLATED = [
    (
        "[None]: No-One can use this feature it is [disabled].",
        "[None]: Никто не может использовать эту функцию она [отключена].",
    ),
    (
        "[Enable/Disable]: Enabling will change the save directory. If [disabled] and then [enabling] on a server.",
        "[Включить/Выключить]: Включение изменит директорию. Если [выключено] и затем [включено] на сервере.",
    ),
    (
        "[Enable/Disable]: If [disabled] will prevent it.\nIf [enabled] will allow it with teleport [enabled].",
        "[Включить/Выключить]: Если [выключен], то запрещает.\nЕсли [включено], то разрешает с телепортацией [включенной].",
    ),
    ("Hold [Shift]", "Halten Sie [Umschalt]"),
]


class Detection(unittest.TestCase):
    def test_invented_tags_are_named(self):
        for source, answer, tags in BAD:
            self.assertEqual(invented_tags(source, answer), tags, answer)

    def test_normal_strings_pass(self):
        self.assertEqual(len(NORMAL), 25)
        for source, answer in NORMAL:
            self.assertEqual(invented_tags(source, answer), [], answer)

    def test_translated_labels_pass(self):
        for source, answer in TRANSLATED:
            self.assertEqual(invented_tags(source, answer), [], answer)

    def test_case_does_not_matter(self):
        self.assertEqual(invented_tags("Set <Name>", "Задать <name>"), [])


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

    def test_tag_rolls_back_is_not_cached_and_goes_to_second_pass(self):
        tr = self._translator("Железный слиток[ru]")
        self.assertEqual(tr.translate("Iron Ingot"), "Iron Ingot")
        self.assertIsNone(tr.cache.get("Iron Ingot", "ru_ru"))
        self.assertEqual(tr.take_rejected(), [("Iron Ingot", "ru_ru")])

    def test_closing_tag_rolls_back(self):
        source, answer = "Ease out bounce function", "Функция ease вы[/ru]"
        tr = self._translator(answer)
        self.assertEqual(tr.translate(source), source)
        self.assertIsNone(tr.cache.get(source, "ru_ru"))

    def test_validation_off_still_rejects(self):
        for answer in ("Железный слиток[ru]", "Железный слиток<ru>"):
            tr = self._translator(answer)
            tr.strict = False
            self.assertEqual(tr.translate("Iron Ingot"), "Iron Ingot", answer)

    def test_tag_from_the_source_is_kept(self):
        tr = self._translator("Удерживайте [Shift]")
        self.assertEqual(tr.translate("Hold [Shift]"), "Удерживайте [Shift]")

    def test_batch_item_rolls_back_and_others_stay(self):
        class Batch(self.Model):
            def chat(self, messages, **kwargs):
                user = messages[-1]["content"]
                if "JSON array" in messages[0]["content"]:
                    return json.dumps(["Золотой слиток[ru]" if "Gold" in x else "ПЕР " + x for x in json.loads(user)],
                                      ensure_ascii=False)
                return "Золотой слиток[ru]" if "Gold" in user else "ПЕР " + user

        tr = Translator(Batch(None), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang="ru_ru")
        tr.max_attempts = 1
        with contextlib.redirect_stdout(io.StringIO()):
            result = tr.translate_many(["Gold Ingot", "Iron Ingot"])
        self.assertEqual(result, ["Gold Ingot", "ПЕР Iron Ingot"])
        self.assertIsNone(tr.cache.get("Gold Ingot", "ru_ru"))


if __name__ == "__main__":
    unittest.main()
