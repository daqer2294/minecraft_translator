"""Тексты от 15 слов сразу идут в сильную модель, если она есть. Иначе всё как раньше."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from src import config, mirrorer
from src.llm import factory
from src.translators import Translator
from src.utils.cache import TranslationCache
from src.utils.markup import text_word_count

LONG = "It climbs trees by hooking on with its sharp claws and shares the berries it gathers with others."
WORDS_15 = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen"
WORDS_14 = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"


class Model:
    def __init__(self, name, reply=None):
        self.name = name
        answers = {"main": "Основной перевод", "strong": "Сильный перевод", "other": "Другой перевод"}
        self.reply = reply or (lambda text: answers[name])
        self.single, self.batches = [], []

    def chat(self, messages, **kwargs):
        text = messages[-1]["content"]
        if "JSON array" in messages[0]["content"]:
            items = json.loads(text)
            self.batches.append(items)
            return json.dumps([self.reply(x) for x in items], ensure_ascii=False)
        self.single.append(text)
        return self.reply(text)


def make(main, strong=None, factory_call=None):
    calls = []

    def get():
        calls.append(1)
        return (strong, "") if strong else (None, "нет модели")

    tr = Translator(main, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                    log=lambda s: None, target_lang="ru_ru", long_text_client=factory_call or get)
    tr.max_attempts = 1
    return tr, calls


class Counting(unittest.TestCase):
    def test_words_without_markup_and_links(self):
        self.assertEqual(text_word_count(WORDS_15), 15)
        self.assertEqual(text_word_count("Deals %s damage with floor(x) from https://a.com/b/c/d/e/f now"), 5)


class Routing(unittest.TestCase):
    def test_threshold_is_15_words(self):
        main, strong = Model("main"), Model("strong")
        tr, _ = make(main, strong)
        tr.translate(WORDS_15)
        tr.translate(WORDS_14)
        self.assertEqual(strong.single, [WORDS_15])
        self.assertEqual(main.single, [WORDS_14])

    def test_no_strong_model_means_as_before(self):
        main = Model("main")
        tr, calls = make(main)
        self.assertEqual(tr.translate(LONG), "Основной перевод")
        self.assertEqual(tr.translate(WORDS_15), "Основной перевод")
        self.assertEqual(calls, [1])

    def test_failure_to_start_means_as_before(self):
        main = Model("main")
        tr, _ = make(main, factory_call=mock.Mock(side_effect=RuntimeError("нет памяти")))
        self.assertEqual(tr.translate(LONG), "Основной перевод")

    def test_client_is_built_once_and_only_when_needed(self):
        main, strong = Model("main"), Model("strong")
        tr, calls = make(main, strong)
        tr.translate("Iron Ingot")
        self.assertEqual(calls, [])
        tr.translate(LONG)
        tr.translate(WORDS_15)
        self.assertEqual(calls, [1])

    def test_explicit_client_wins(self):
        main, strong, other = Model("main"), Model("strong"), Model("other")
        tr, _ = make(main, strong)
        self.assertEqual(tr.translate(LONG, client=other), "Другой перевод")
        self.assertEqual(strong.single, [])

    def test_setting_turns_it_off(self):
        main, strong = Model("main"), Model("strong")
        with mock.patch.object(config, "LONG_TEXT_STRONG", False):
            tr, _ = make(main, strong)
        self.assertEqual(tr.translate(LONG), "Основной перевод")

    def test_threshold_comes_from_config(self):
        main, strong = Model("main"), Model("strong")
        with mock.patch.object(config, "LONG_TEXT_MIN_WORDS", 2):
            tr, _ = make(main, strong)
        self.assertEqual(tr.translate("Iron Ingot"), "Сильный перевод")

    def test_translator_without_a_factory_works_as_before(self):
        main = Model("main")
        tr = Translator(main, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang="ru_ru")
        self.assertEqual(tr.translate(LONG), "Основной перевод")

    def test_long_text_from_cache_asks_nobody(self):
        main, strong = Model("main"), Model("strong")
        tr, calls = make(main, strong)
        tr.cache.put(LONG, "Готовый перевод", "ru_ru")
        self.assertEqual(tr.translate(LONG), "Готовый перевод")
        self.assertEqual(calls, [])


class Batch(unittest.TestCase):
    def test_long_goes_alone_to_strong_and_short_in_a_batch_to_main(self):
        main, strong = Model("main"), Model("strong")
        tr, _ = make(main, strong)
        with contextlib.redirect_stdout(io.StringIO()):
            result = tr.translate_many(["Iron Ingot", LONG, "Gold Ingot"])
        self.assertEqual(result, ["Основной перевод", "Сильный перевод", "Основной перевод"])
        self.assertEqual(strong.single, [LONG])
        self.assertEqual(strong.batches, [])
        self.assertEqual(main.batches, [["Iron Ingot", "Gold Ingot"]])

    def test_no_strong_model_keeps_long_in_the_batch(self):
        main = Model("main")
        tr, _ = make(main)
        with contextlib.redirect_stdout(io.StringIO()):
            tr.translate_many(["Iron Ingot", LONG])
        self.assertEqual(main.batches, [["Iron Ingot", LONG]])


class Rejected(unittest.TestCase):
    def test_defect_of_the_strong_model_is_not_asked_again(self):
        main, strong = Model("main"), Model("strong", lambda text: "Перевод с браком[ru]")
        tr, _ = make(main, strong)
        self.assertEqual(tr.translate(LONG), LONG)
        self.assertIsNone(tr.cache.get(LONG, "ru_ru"))
        self.assertEqual(tr.take_rejected(), [])

    def test_defect_of_the_main_model_still_goes_to_second_pass(self):
        main = Model("main", lambda text: "Перевод с браком[ru]")
        tr, _ = make(main)
        self.assertEqual(tr.translate(LONG), LONG)
        self.assertEqual(tr.take_rejected(), [(LONG, "ru_ru")])


class SharedClient(unittest.TestCase):
    def test_one_client_for_the_run(self):
        built = [(Model("strong"), "")]
        with mock.patch.object(factory, "build_strong_client", side_effect=built) as build:
            get = factory.shared_strong_client(object())
            self.assertIs(get()[0], get()[0])
        self.assertEqual(build.call_count, 1)

    def test_absent_model_is_remembered_too(self):
        with mock.patch.object(factory, "build_strong_client", return_value=(None, "нет")) as build:
            get = factory.shared_strong_client(object())
            self.assertEqual(get(), (None, "нет"))
            self.assertEqual(get(), (None, "нет"))
        self.assertEqual(build.call_count, 1)


class Pipeline(unittest.TestCase):
    def test_jar_uses_both_models_and_second_pass_does_not_repeat(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        os.makedirs(os.path.join(base, "mods"))
        with zipfile.ZipFile(os.path.join(base, "mods", "m.jar"), "w") as z:
            z.writestr("assets/m/lang/en_us.json", json.dumps({"short": "Iron Ingot", "long": LONG, "bad": WORDS_15}))
        main = Model("main")
        strong = Model("strong", lambda text: "Перевод с браком[ru]" if text == WORDS_15 else "Сильный перевод")
        get = factory.shared_strong_client(object())
        with mock.patch.object(factory, "build_strong_client", return_value=(strong, "")) as build:
            tr = Translator(main, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                            log=lambda s: None, target_lang="ru_ru", long_text_client=get)
            tr.max_attempts = 1
            with contextlib.redirect_stdout(io.StringIO()):
                mirrorer.mirror_translate_dir(base, out, tr, log=lambda s: None, target_lang="ru_ru", strong_client=get)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(sorted(strong.single), sorted([LONG, WORDS_15]))
        self.assertEqual(main.single, ["Iron Ingot"])
        name = [n for n in os.listdir(out) if n.endswith(".zip")][0]
        with zipfile.ZipFile(os.path.join(out, name)) as z:
            pack = json.loads(z.read("assets/m/lang/ru_ru.json"))
        self.assertEqual(pack, {"short": "Основной перевод", "long": "Сильный перевод"})


if __name__ == "__main__":
    unittest.main()
