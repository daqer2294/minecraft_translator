"""Файл общих терминов: формы совпадают с утверждённым списком."""
from __future__ import annotations

import re
import unittest

from src.glossary import load_fixed_terms


class Fixed(unittest.TestCase):
    def setUp(self):
        self.terms = {t.en: t for t in load_fixed_terms("ru_ru")}

    def test_agreed_forms(self):
        expected = {
            "user": "пользователь", "target": "цель", "flinch": "вздрогнуть", "stat": "показатель",
            "Attack": "Атака", "Defense": "Защита", "Sp. Atk": "Спец. атака", "Sp. Def": "Спец. защита",
            "Speed": "Скорость", "paralysis": "паралич", "critical hit": "критический удар",
            "headbutt": "удар головой", "faint": "потерять сознание", "move": "приём", "Pokémon": "покемон",
        }
        self.assertEqual({en: t.ru for en, t in self.terms.items()}, expected)

    def test_player_is_not_user(self):
        self.assertNotIn("игрок", self.terms["user"].ru)
        self.assertNotIn("игрок", self.terms["user"].roots)

    def test_roots_are_lowercase_russian_without_yo(self):
        for term in self.terms.values():
            for root in term.roots:
                self.assertRegex(root, r"^[а-я]+$", term.en)
                self.assertNotIn("ё", root)

    def test_every_root_fits_the_russian_word(self):
        for en, term in self.terms.items():
            if en == "paralysis":
                continue
            text = term.ru.lower().replace("ё", "е")
            for root in term.roots:
                self.assertIn(root, text, en)

    def test_verb_moves_is_not_a_form(self):
        self.assertNotIn("moves", self.terms["move"].forms)
        self.assertNotIn("move", self.terms["move"].forms)

    def test_prompt_only_terms(self):
        self.assertEqual({en for en, t in self.terms.items() if not t.check}, {"stat", "Pokémon"})

    def test_language_without_a_file_has_no_fixed_terms(self):
        self.assertEqual(load_fixed_terms("de_de"), [])


if __name__ == "__main__":
    unittest.main()
