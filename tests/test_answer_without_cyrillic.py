"""Ответ без единой кириллической буквы на русский язык. Пары BAD взяты из настоящего кэша."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

from src.translators import Translator
from src.utils.cache import TranslationCache
from src.utils.markup import answer_without_cyrillic, has_intro_or_raw_mask
from tools.clean_mixed_cache import rejection_reason

MAGMAR = (
    "Legend has it that this Pokémon was born from the crater of a volcano. When wounded, it bathes in lava.",
    "Legends say that this Pokémon was born from the crater of a volcano. When wounded, it immerses in lava.",
)
SILCOON = (
    "Wraps itself in thin strings of silk while it stores energy for evolution. It can't extend its tongue.",
    "wraps itself in thin strings of silk while it stores energy for evolution. It can't extend its tongue.",
)
BAD = [
    MAGMAR,
    SILCOON,
    ("Wanders Water", "wanderst vodu"),
    ("Fights Melee", "fights Melee"),
    ("Is Baby: %s", "Baby: %s"),
    ("Ticks per player update", "ticks per player update"),
    ("Tea Seasoning Items", "Tea seasoning items"),
    ("Scroll bar", "-scrollbar"),
    ("sqrt(x)\nGet the square root of x", "sqrt(x)\n√x"),
    ("tan(x)\nGet the tangent of x", "tan(x)\ntg x"),
]

# Перевод, равный источнику или латинский по определению, и обычные русские ответы.
NORMAL = [
    ("Ctrl + G", "Ctrl + G"),
    ("Ctrl + Shift", "Ctrl+Shift"),
    ("CTRL + ALT + R", "CTRL+ALT+R"),
    ("Shift + Left Click", "Shift+Left Click"),
    ("Mod Menu", "Mod Menu"),
    ("Mod Menu", "Mod Menu:"),
    ("Fabric API", "Fabric API."),
    ("Entity Model Features", "Entity Model Features"),
    ("Falling Leaves", "Falling Leaves!"),
    ("Milli Buckets", "mB"),
    ("Kilo Bytes", "KB"),
    ("Frames per second", "fps"),
    ("Speed: 10 km", "10 km"),
    ("/cp search <searchTerm>", "/cp search <searchTerm>"),
    ("Run /give", "/give"),
    ("Raw: %s", "Сырое: %s"),
    ("Ticks", "ticks"),
    ("Wanders", "wandering"),
    ("Iron Ingot", "Железный слиток"),
    ("See https://example.com", "https://example.com"),
    ("Open the Pokédex now", "Открой Pokédex"),
    ("Some long English sentence that the model keeps as is", "Some long English sentence that the model keeps as is"),
    ("minecraft:stone block", "minecraft:stone"),
    ("100 200", "300"),
    ("%s %d", "%s"),
]


class Rule(unittest.TestCase):
    def test_real_defects(self):
        self.assertEqual(len(BAD), 10)
        for source, answer in BAD:
            self.assertTrue(answer_without_cyrillic(source, answer, "ru_ru"), answer)

    def test_normal_answers(self):
        self.assertEqual(len(NORMAL), 25)
        for source, answer in NORMAL:
            self.assertFalse(answer_without_cyrillic(source, answer, "ru_ru"), (source, answer))

    def test_only_cyrillic_targets(self):
        for lang in ("de_de", "fr_fr", "pt_br", "zh_cn", "ja_jp", "sr_cs", ""):
            self.assertFalse(answer_without_cyrillic("Fights Melee", "Kämpft im Nahkampf", lang), lang)
        for lang in ("ru_ru", "RU_RU", "uk_ua", "be_by", "bg_bg", "sr_sp"):
            self.assertTrue(answer_without_cyrillic("Fights Melee", "fights Melee", lang), lang)

    def test_name_with_many_words_is_not_a_name(self):
        source = "Some long English sentence here now"
        self.assertTrue(answer_without_cyrillic(source, source + ".", "ru_ru"))

    def test_links_and_code_are_not_words(self):
        self.assertFalse(answer_without_cyrillic("https://a.com/one/two Docs", "https://a.com/one/two Docs!", "ru_ru"))
        self.assertFalse(answer_without_cyrillic("floor(x) Docs", "floor(x)", "ru_ru"))

    def test_ready_to_translate_is_an_intro(self):
        answer = "I'm ready to translate. What's the text you'd like me to translate? %s"
        self.assertTrue(has_intro_or_raw_mask("Raw: %s", answer))
        self.assertTrue(has_intro_or_raw_mask("Raw: %s", "I’m ready to translate."))
        self.assertFalse(has_intro_or_raw_mask("Ready to translate", "Готово к переводу"))

    def test_cleanup_names_the_reason(self):
        self.assertEqual(rejection_reason(*MAGMAR, "ru_ru"), "ответ без кириллицы")
        self.assertIsNone(rejection_reason("Ctrl + G", "Ctrl+G", "ru_ru"))
        self.assertIn("вступление", rejection_reason("Raw: %s", "I'm ready to translate. %s", "ru_ru"))


class Model:
    name = "m"

    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, **kwargs):
        if "JSON array" in messages[0]["content"]:
            return json.dumps([self.reply(x) for x in json.loads(messages[-1]["content"])], ensure_ascii=False)
        return self.reply(messages[-1]["content"])


class Translation(unittest.TestCase):
    def _translator(self, reply, lang="ru_ru"):
        tr = Translator(Model(reply), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang=lang)
        tr.max_attempts = 1
        return tr

    def test_rolls_back_is_not_cached_and_goes_to_second_pass(self):
        tr = self._translator(lambda text: SILCOON[1])
        self.assertEqual(tr.translate(SILCOON[0]), SILCOON[0])
        self.assertIsNone(tr.cache.get(SILCOON[0], "ru_ru"))
        self.assertEqual(tr.take_rejected(), [(SILCOON[0], "ru_ru")])

    def test_validation_off_still_rejects(self):
        tr = self._translator(lambda text: "fights Melee")
        tr.strict = False
        self.assertEqual(tr.translate("Fights Melee"), "Fights Melee")

    def test_batch_item_rolls_back_and_others_stay(self):
        tr = self._translator(lambda text: "fights Melee" if "Fights" in text else "ПЕР " + text)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tr.translate_many(["Fights Melee", "Iron Ingot"]), ["Fights Melee", "ПЕР Iron Ingot"])
        self.assertIsNone(tr.cache.get("Fights Melee", "ru_ru"))

    def test_german_target_is_not_affected(self):
        tr = self._translator(lambda text: "Kämpft im Nahkampf", "de_de")
        self.assertEqual(tr.translate("Fights Melee", "de_de"), "Kämpft im Nahkampf")


if __name__ == "__main__":
    unittest.main()
