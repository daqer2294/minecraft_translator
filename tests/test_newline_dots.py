"""Точка сразу после переноса в ответе модели: «кожи игрока\\n. Примечание». Записи REAL из кэша дословно."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

from src.translators import Translator, _extract_tokens
from src.utils.cache import TranslationCache
from src.utils.markup import strip_newline_dots

REAL = [
    (
        "This setting will prevent ETF from loading overrides for the 'base' texture if present in the "
        "optifine/etf directories\nE.G. optifine/random/entity/chicken.png will not act as an override to "
        "the vanilla texture.\nON is the default OptiFine behaviour.",
        "Эта настройка предотвратит загрузку перекрытий для текстуры 'базового' состояния, если они "
        "присутствуют в директориях optifine/etf\n. Например, optifine/random/entity/chicken.png не будет "
        "действовать как перекрытие для ванильной текстуры.\nON является стандартным поведением OptiFine.",
    ),
    (
        "Expanded Radar Only!\nWhether underground players are visible on the radar.\nNether is unaffected "
        "by this setting.\nALL: Everyone can see underground players\nOP: Only Ops can see underground "
        "players\nNONE: No one can see underground players.",
        "Расширенный радар только!\n.\nНевидимы ли игроки под землей на радаре.\n.НИЧЬЯ: Всякий может "
        "видеть игроков под землей\n.ОП: Только Опс могут видеть игроков под землей\n.НИКОГО: Никто не "
        "может видеть игроков под землей.",
    ),
    (
        "Block Below Solid \nVary based on the first solid non-air block below the entity, no matter the "
        "distance, only triggers for the first solid block found.\nExample: "
        "blockBelowSolid.1=oak_leaves\n§ablockBelowSolid.<n>=<list>",
        "Блок ниже твёрдый \n. Изменяется в зависимости от первого твёрдого блока ниже сущности, "
        "независимо от расстояния, активируется только для первого найденного твёрдого блока.\nПример: "
        "blockBelowSolid.1=oak_leaves\n§ablockBelowSolid.<n>=<list>",
    ),
    (
        "Customize dynamic images to display in the RPC\\n Notes:\\n - Any placeholder from other modules "
        "can be used (Globals, Biomes, Dimensions, etc)",
        "Динамические изображения можно настроить для отображения в RPC\\n. Примечание: \\n. В любом из "
        "модулей можно использовать плейсхолдеры (Globals, Biomes, Dimensions и т.п.)",
    ),
    (
        "The endpoint URL to use for retrieving server icons\\n Notes:\\n - This endpoint is used for "
        "Scroll List Previews for non-base64 Server Icons and the \"server.icon\" placeholder, if not "
        "overriden",
        "URL для обращения к серверным иконкам\\n. Примечание:\\n - Этот URL используется для "
        "предпросмотров списка ролловер для не-баз64-иконок серверов и плейсхолдера \"server.icon\", если он "
        "не переопределен.",
    ),
    (
        "The endpoint URL to use for retrieving player skins\\n Notes:\\n - This endpoint is used for "
        "Scroll List Previews and the \"player.icon\" placeholder, if not overriden",
        "URL для получения кожи игрока\\n. Примечание: \\n. Этот URL используется для предпросмотров "
        "списка прокрутки и плейсхолдера \"player.icon\", если он не переопределен.",
    ),
]

# В источнике есть точка после переноса: ответ не меняется, в обеих формах переноса.
SOURCE_HAS_DOT = [
    ("Path:\n.minecraft/config", "Путь:\n.minecraft/config"),
    ("Path:\\n.minecraft/config", "Путь:\\n.minecraft/config"),
    ("Hidden file\n.gitignore", "Скрытый файл\n.gitignore"),
    ("Hidden file\\n.gitignore", "Скрытый файл\\n.gitignore"),
    ("Scale\n.5 of the size", "Масштаб\n.5 от размера"),
    ("Scale\\n.5 of the size", "Масштаб\\n.5 от размера"),
    ("Line one\n. Line two", "Первая строка\n. Вторая строка"),
    ("Line one\\n. Line two", "Первая строка\\n. Вторая строка"),
    ("First\n.\nThird", "Первая\n.\nТретья"),
    ("First\\n.\\nThird", "Первая\\n.\\nТретья"),
    ("Dotted list\n.Item one\n.Item two", "Список\n.Первый\n.Второй"),
    ("Dotted list\\n.Item one\\n.Item two", "Список\\n.Первый\\n.Второй"),
    ("Wait\n...", "Подождите\n..."),
    ("Wait\\n...", "Подождите\\n..."),
    ("Ends with a dot\n.", "Заканчивается точкой\n."),
    ("Ends with a dot\\n.", "Заканчивается точкой\\n."),
    ("§aFirst\n.§bSecond", "§aПервая\n.§bВторая"),
    ("§aFirst\\n.§bSecond", "§aПервая\\n.§bВторая"),
    ("Value %s\n. Other %d", "Значение %s\n. Другое %d"),
    ("Value %s\\n. Other %d", "Значение %s\\n. Другое %d"),
    (".\n.", ".\n."),
    ("One.\n.Two.", "Один.\n.Два."),
    ("Use [Shift]\n. Then <name>", "Нажмите [Shift]\n. Затем <name>"),
    ("Mixed\nforms\\n. here", "Смешанные\nформы\\n. здесь"),
    ("Translated differently\n. Here", "Переведено иначе\n.Здесь"),
]

# В источнике такой точки нет, но и эти ответы менять нельзя.
KEPT = [
    ("Folder\n minecraft", "Папка\n.minecraft"),
    ("Folder\\n minecraft", "Папка\\n.minecraft"),
    ("Half\n five", "Половина\n.5"),
    ("Half\\n five", "Половина\\n.5"),
    ("Wait\n now", "Подождите\n..."),
    ("Wait\\n now", "Подождите\\n..."),
    ("End of line.\nNext.", "Конец строки.\nСледующая."),
    ("End of line.\\nNext.", "Конец строки.\\nСледующая."),
    ("Version 1.5", "Версия 1.5"),
    ("One line", "Одна строка."),
]


class Rule(unittest.TestCase):
    def test_real_entries_lose_only_the_dots(self):
        self.assertEqual(len(REAL), 6)
        for source, answer in REAL:
            result = strip_newline_dots(source, answer)
            self.assertNotEqual(result, answer)
            self.assertNotIn("\\n.", result)
            self.assertNotIn("\n.", result)
            self.assertEqual(_extract_tokens(result), _extract_tokens(source), source)
            self.assertEqual(_extract_tokens(result), _extract_tokens(answer), source)
            removed = answer.count("\\n.") + answer.count("\n.")
            self.assertEqual(len(answer) - len(result), removed)
            self.assertEqual(answer.replace("\\n.", "\\n").replace("\n.", "\n"), result)

    def test_source_with_the_dot_stays_as_is(self):
        self.assertEqual(len(SOURCE_HAS_DOT), 25)
        for source, answer in SOURCE_HAS_DOT:
            self.assertEqual(strip_newline_dots(source, answer), answer, source)

    def test_hidden_files_numbers_and_ellipsis_stay(self):
        for source, answer in KEPT:
            self.assertEqual(strip_newline_dots(source, answer), answer, answer)

    def test_dot_before_other_text_goes(self):
        for tail in (" Текст", "Текст", "текст", "Minecraft", "§aТекст", "\n", "\\n", ""):
            self.assertEqual(strip_newline_dots("One\nTwo", "Один\n." + tail), "Один\n" + tail, tail)
            self.assertEqual(strip_newline_dots("One\\nTwo", "Один\\n." + tail), "Один\\n" + tail, tail)

    def test_only_kept_kinds_survive_in_one_answer(self):
        self.assertEqual(strip_newline_dots("A\nB\nC", "А\n. Б\n.minecraft"), "А\n Б\n.minecraft")

    def test_empty_answer_passes_through(self):
        self.assertIsNone(strip_newline_dots("One\nTwo", None))
        self.assertEqual(strip_newline_dots("One\nTwo", ""), "")


class Model:
    name = "m"

    def __init__(self, answer):
        self.answer = answer

    def chat(self, messages, **kwargs):
        if "JSON array" in messages[0]["content"]:
            return json.dumps([self.answer for _ in json.loads(messages[-1]["content"])], ensure_ascii=False)
        return self.answer


class Translation(unittest.TestCase):
    def _translator(self, answer):
        tr = Translator(Model(answer), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang="ru_ru")
        tr.max_attempts = 1
        return tr

    def test_single_answer_is_cleaned_before_the_cache(self):
        tr = self._translator("Первая[[0]]. Вторая")
        self.assertEqual(tr.translate("First\\nSecond"), "Первая\\n Вторая")
        self.assertEqual(tr.cache.get("First\\nSecond", "ru_ru"), "Первая\\n Вторая")

    def test_batch_answer_is_cleaned_before_the_cache(self):
        tr = self._translator("Первая[[0]]. Вторая")
        with contextlib.redirect_stdout(io.StringIO()):
            result = tr.translate_many(["First\nSecond", "Third\nFourth"])
        self.assertEqual(result, ["Первая\n Вторая", "Первая\n Вторая"])
        self.assertEqual(tr.cache.get("First\nSecond", "ru_ru"), "Первая\n Вторая")

    def test_source_with_the_dot_keeps_it(self):
        tr = self._translator("Первая[[0]]. Вторая")
        self.assertEqual(tr.translate("First\\n. Second"), "Первая\\n. Вторая")

    def test_validators_still_see_the_answer(self):
        tr = self._translator("Первая[[0]]. Вторая[ru]")
        self.assertEqual(tr.translate("First\\nSecond"), "First\\nSecond")
        self.assertIsNone(tr.cache.get("First\\nSecond", "ru_ru"))

    def test_cached_value_is_returned_untouched(self):
        tr = self._translator("не должно спрашиваться")
        tr.cache.put("First\nSecond", "Первая\n. Вторая", "ru_ru")
        self.assertEqual(tr.translate("First\nSecond"), "Первая\n. Вторая")


if __name__ == "__main__":
    unittest.main()
