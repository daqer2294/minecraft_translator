"""Строгий валидатор: видит всю разметку и не срабатывает на обычном тексте."""
from __future__ import annotations

import os
import tempfile
import unittest

from src.translators import Translator, _extract_tokens
from src.utils.cache import TranslationCache


class Tokens(unittest.TestCase):
    def test_every_kind_of_markup_is_seen(self):
        cases = {
            "Hello %s": ["%s"],
            "%1$s and %02d": ["%02d", "%1$s"],
            "{0} and {count}": ["{0}", "{count}"],
            "§aGreen": ["§a"],
            "&6Gold": ["&6"],
            "$(item)Iron$()": ["$()", "$(item)"],
            "{@pagebreak}Next": ["{@pagebreak}"],
            "Use <red>this</red>": ["</red>", "<red>"],
            "One\nTwo": ["\n"],
            "One\\nTwo": ["\\n"],
            "See minecraft:stone": ["minecraft:stone"],
            "100%%": ["%%"],
        }
        for text, expected in cases.items():
            self.assertEqual(sorted(_extract_tokens(text)), sorted(expected), text)

    def test_ordinary_text_has_no_false_tokens(self):
        for text in ("Ratio 1:2 is ok", "Note:example text", "Time 12:30", "100% sure", "Sale 10%off",
                     "Salt & pepper", "a < b", "Just text"):
            self.assertEqual(_extract_tokens(text), (), text)

    def test_order_does_not_matter(self):
        self.assertEqual(_extract_tokens("%s then {0}"), _extract_tokens("{0} потом %s"))

    def test_count_matters(self):
        self.assertNotEqual(_extract_tokens("§a one §a two"), _extract_tokens("§a один два"))


class Scripted:
    name = "scripted"

    def __init__(self, answer):
        self.answer = answer

    def chat(self, messages, **kwargs):
        return self.answer


def translate(text, answer):
    tr = Translator(
        Scripted(answer), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
        strict=True, log=lambda s: None, target_lang="ru_ru",
    )
    tr.max_attempts = 1
    return tr.translate(text)


class FinalCheck(unittest.TestCase):
    """Модель теряет токен маски, затем повтор без маски портит разметку: остаётся оригинал."""

    def test_damaged_markup_rolls_back(self):
        for text, answer in (
            ("One §a two", "Раз два"),
            ("One &6 two", "Раз два"),
            ("One\nTwo", "Раз Два"),
            ("One\\nTwo", "Раз Два"),
            ("One $(item) two", "Раз два"),
            ("One <red> two", "Раз два"),
            ("One {@pagebreak} two", "Раз два"),
        ):
            self.assertEqual(translate(text, answer), text, text)

    def test_translated_ratio_is_not_rolled_back(self):
        self.assertEqual(translate("Ratio 1:2 is ok", "Отношение 1 : 2"), "Отношение 1 : 2")
        self.assertEqual(translate("Note:example text", "Примечание: текст"), "Примечание: текст")


if __name__ == "__main__":
    unittest.main()
