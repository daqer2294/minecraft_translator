"""Выражения кода в строках не уходят в модель, а обычные скобки остаются текстом."""
from __future__ import annotations

import os
import tempfile
import unittest

from src.translators import Translator, _extract_tokens
from src.utils.cache import TranslationCache
from src.utils.helpers import is_untranslatable
from src.utils.markup import mask, unmask

EXAMPLE = (
    "cubicbezier(k, x, y, z, w)\n"
    "Interpolates between x and y using the cubic bezier spline function with control points z and w"
)

CODE = [
    "cubicbezier(k, x, y, z, w)",
    "clamp(x, min, max)",
    "pow(x, 2)",
    "frac(x)",
    "foo()",
    "mcTranslate(input=String, args=Object...)",
    "asIdentifier(target, formatToId ?: false, avoid ?: null)",
    "convertTimeFormat(dateString, fromFormat, toFormat)",
    "getNbt(data=Entity|ItemStack, path=String...)",
    "keyframeloop(k, a, b, c,...)",
    "Math.sin(x)",
    "player.getName()",
    "console.log()",
    "dateString",
    "fromFormat",
    "mobArmorSpawnRate",
    "canSpawnWeapon",
    "entity_id",
    "urn_spawn",
    "golden_sacrificial_bowl",
    "MAX_VALUE",
]

NORMAL_BRACKETS = [
    "Moderate (1.5)",
    "Show all (default)",
    "Item(s)",
    "block(s) broken",
    "chunk(s)",
    "entity(ies)",
    "Level (max 5)",
    "Damage (x2)",
    "Speed (%)",
    "Hold (Shift)",
    "Size(1.5)",
    "Sword(Diamond)",
    "Click here (optional)",
    "Enable (recommended)",
    "Health (HP)",
    "Mode: Fast (low)",
    "Range (blocks)",
    "Gold Ingot (x4)",
    "Note (see below)",
    "Use Item (Right Click)",
    "Reload (R)",
    "(optional) Text",
    "One (1) item",
    "skulls(appending facing direction at the end)",
    "Cans(Common)",
    "Pin(k)",
]

NORMAL_WORDS = [
    "Light_gray Concrete",
    "Garland White Lights (Diagonal_Right)",
    "Iron Ingot",
    "Don't stop",
    "YouTube channel",
    "McDonald's farm",
    "Ratio 1:2 is ok",
    "Plain text, with commas",
]


class Detection(unittest.TestCase):
    def test_code_is_a_token(self):
        for text in CODE:
            m = mask(f"Use {text} here")
            self.assertEqual(m.tokens, [text], text)

    def test_normal_brackets_are_not_tokens(self):
        self.assertGreaterEqual(len(NORMAL_BRACKETS), 20)
        for text in NORMAL_BRACKETS:
            self.assertEqual(_extract_tokens(text), (), text)

    def test_normal_words_are_not_tokens(self):
        for text in NORMAL_WORDS:
            self.assertEqual(_extract_tokens(text), (), text)

    def test_code_right_after_a_color_code_is_protected(self):
        self.assertEqual(mask("Call &oconsole.log()&r to print").tokens, ["&o", "console.log()", "&r"])
        self.assertEqual(mask("Call &oidContext&r now").tokens, ["&o", "idContext", "&r"])
        self.assertEqual(mask("A\\nfooBar b").tokens, ["\\n", "fooBar"])
        self.assertEqual(mask("Has: %smB of %s now").tokens, ["%s", "mB", "%s"])

    def test_namespaced_id_still_wins(self):
        self.assertEqual(mask("Use minecraft:stone_bricks here").tokens, ["minecraft:stone_bricks"])

    def test_example_sends_only_the_sentence_to_the_model(self):
        m = mask(EXAMPLE)
        self.assertEqual(m.prefix, "cubicbezier(k, x, y, z, w)\n")
        self.assertEqual(m.core, EXAMPLE.split("\n", 1)[1])
        self.assertEqual(m.tokens, [])

    def test_only_code_needs_no_model(self):
        for text in ("clamp(x, min, max)", "dateString", "convertTimeFormat(dateString, fromFormat, toFormat)"):
            self.assertTrue(is_untranslatable(text), text)
        for text in NORMAL_BRACKETS:
            if text not in ("Speed (%)",):
                self.assertFalse(is_untranslatable(text), text)


class Translation(unittest.TestCase):
    class Model:
        name = "model"

        def __init__(self, reply):
            self.reply, self.sent = reply, []

        def chat(self, messages, **kwargs):
            self.sent.append(messages[-1]["content"])
            return self.reply(messages[-1]["content"])

    def _translator(self, reply):
        client = self.Model(reply)
        tr = Translator(client, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang="ru_ru")
        tr.max_attempts = 1
        return tr, client

    def test_example_keeps_the_signature(self):
        tr, client = self._translator(lambda t: "Интерполирует между значениями")
        self.assertEqual(tr.translate(EXAMPLE), "cubicbezier(k, x, y, z, w)\nИнтерполирует между значениями")
        self.assertNotIn("cubicbezier", client.sent[0])

    def test_inner_code_survives_when_model_keeps_the_token(self):
        tr, client = self._translator(lambda t: t.replace("Use", "Используй").replace("here", "здесь"))
        self.assertEqual(tr.translate("Use fooBar here"), "Используй fooBar здесь")
        self.assertEqual(client.sent, ["Use [[0]] here"])

    def test_damaged_code_rolls_back_and_is_not_cached(self):
        for answer in ("Используй фооБар здесь", "Используй здесь"):
            tr, _ = self._translator(lambda t: answer)
            self.assertEqual(tr.translate("Use fooBar here"), "Use fooBar here", answer)
            self.assertIsNone(tr.cache.get("Use fooBar here", "ru_ru"), answer)

    def test_lost_token_is_retried_and_damage_goes_to_the_second_pass(self):
        tr, client = self._translator(lambda t: "Используй фооБар")
        tr.translate("Use fooBar here")
        self.assertEqual(client.sent, ["Use [[0]] here", "Use fooBar here"])
        self.assertEqual(tr.take_rejected(), [("Use fooBar here", "ru_ru")])

    def test_normal_bracket_reaches_the_model_whole(self):
        tr, client = self._translator(lambda t: "Средний (1.5)")
        self.assertEqual(tr.translate("Moderate (1.5)"), "Средний (1.5)")
        self.assertEqual(client.sent, ["Moderate (1.5)"])

    def test_string_of_only_code_is_returned_without_request(self):
        tr, client = self._translator(lambda t: "кламп")
        self.assertEqual(tr.translate("clamp(x, min, max)"), "clamp(x, min, max)")
        self.assertEqual(client.sent, [])

    def test_unmask_restores_exactly(self):
        for text in CODE:
            full = f"Use {text} here"
            m = mask(full)
            self.assertEqual(unmask(m.core, m), full, text)


if __name__ == "__main__":
    unittest.main()
