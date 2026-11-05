"""Сбор терминов из официальных языковых файлов: ничего не придумывается."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile

from src.glossary import Collector, collect_official, read_vanilla_lang

EN = {
    "cobblemon.species.bulbasaur.name": "Bulbasaur",
    "cobblemon.species.bulbasaur.desc": "A long description",
    "cobblemon.move.headbutt": "Headbutt",
    "cobblemon.move.headbutt.desc": "The user sticks out its head.",
    "cobblemon.ability.static": "Static",
    "cobblemon.type.fire": "Fire",
    "item.cobblemon.poke_ball": "Poké Ball",
    "item.cobblemon.link_cable.tooltip": "Evolves pokemon",
    "block.cobblemon.apricorn_log": "Apricorn Log",
    "cobblemon.ui.moves": "Moves",
}
RU = {
    "cobblemon.species.bulbasaur.name": "Бульбазавр",
    "cobblemon.species.bulbasaur.desc": "Длинное описание",
    "cobblemon.move.headbutt": "Удар головой",
    "cobblemon.ability.static": "Статическое электричество",
    "cobblemon.type.fire": "Огнен.",
    "item.cobblemon.poke_ball": "Покебол",
    "item.cobblemon.link_cable.tooltip": "Эволюционирует покемонов",
    "block.cobblemon.apricorn_log": "Apricorn Log",
    "cobblemon.ui.moves": "Приёмы",
}


def jar_bytes(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        for name, content in files.items():
            z.writestr(name, content if isinstance(content, (str, bytes)) else json.dumps(content, ensure_ascii=False))
    return data.getvalue()


class Categories(unittest.TestCase):
    def setUp(self):
        self.c = Collector()
        self.c.add_files(EN, RU)

    def test_names_are_taken_with_their_category(self):
        terms = {t.en: (t.ru, t.category) for t in self.c.terms.values()}
        self.assertEqual(terms, {
            "Bulbasaur": ("Бульбазавр", "pokemon"),
            "Headbutt": ("Удар головой", "move"),
            "Static": ("Статическое электричество", "ability"),
            "Poké Ball": ("Покебол", "item"),
        })

    def test_descriptions_and_ui_are_not_terms(self):
        self.assertNotIn("A long description", self.c.terms)
        self.assertNotIn("Moves", self.c.terms)
        self.assertNotIn("Evolves pokemon", self.c.terms)

    def test_abbreviation_and_english_value_count_as_missing(self):
        self.assertEqual(self.c.missing_counts()["type"], 1)
        self.assertEqual(self.c.missing_counts()["block"], 1)

    def test_key_absent_from_ru_counts_as_missing(self):
        c = Collector()
        c.add_files({"cobblemon.species.x.name": "Xname"}, {})
        self.assertEqual(c.missing_counts()["pokemon"], 1)
        self.assertEqual(c.terms, {})

    def test_placeholder_is_not_a_term(self):
        c = Collector()
        c.add_files({"cobblemon.type.suffix": "%1$s Type"}, {"cobblemon.type.suffix": "%1$s тип"})
        self.assertEqual(c.terms, {})

    def test_conflict_keeps_the_stronger_category(self):
        c = Collector()
        c.add_files({"item.a.fly": "Fly"}, {"item.a.fly": "Муха"})
        c.add_files({"cobblemon.move.fly": "Fly"}, {"cobblemon.move.fly": "Полёт"})
        self.assertEqual((c.terms["Fly"].ru, c.terms["Fly"].category), ("Полёт", "move"))
        self.assertEqual(c.conflicts, 1)
        c.add_files({"block.a.fly": "Fly"}, {"block.a.fly": "Другое"})
        self.assertEqual(c.terms["Fly"].ru, "Полёт")

    def test_found_elsewhere_is_not_missing(self):
        c = Collector()
        c.add_files({"item.a.x": "Some Item"}, {})
        c.add_files({"item.b.x": "Some Item"}, {"item.b.x": "Предмет"})
        self.assertEqual(c.missing_counts()["item"], 0)


class Archives(unittest.TestCase):
    def test_nested_jar_broken_jar_and_jar_without_ru(self):
        folder = tempfile.mkdtemp()
        inner = jar_bytes({
            "assets/i/lang/en_us.json": {"item.i.gem": "Gem"},
            "assets/i/lang/ru_ru.json": {"item.i.gem": "Самоцвет"},
        })
        with open(os.path.join(folder, "a.jar"), "wb") as f:
            f.write(jar_bytes({
                "fabric.mod.json": {"jars": [{"file": "META-INF/jars/i.jar"}]},
                "META-INF/jars/i.jar": inner,
                "assets/a/lang/en_us.json": EN,
                "assets/a/lang/ru_ru.json": RU,
            }))
        with open(os.path.join(folder, "b.jar"), "wb") as f:
            f.write(b"not a jar")
        with open(os.path.join(folder, "c.jar"), "wb") as f:
            f.write(jar_bytes({"assets/c/lang/en_us.json": {"item.c.x": "No Translation"}}))
        c = collect_official([os.path.join(folder, n) for n in ("a.jar", "b.jar", "c.jar", "absent.jar")], "ru_ru")
        self.assertEqual(c.terms["Gem"].ru, "Самоцвет")
        self.assertEqual(c.terms["Bulbasaur"].ru, "Бульбазавр")
        self.assertNotIn("No Translation", c.terms)


class Vanilla(unittest.TestCase):
    def _install(self):
        root = tempfile.mkdtemp()
        base = os.path.join(root, "Instances", "Pack")
        os.makedirs(base)
        install = os.path.join(root, "Install")
        os.makedirs(os.path.join(install, "versions", "1.21.1"))
        os.makedirs(os.path.join(install, "assets", "indexes"))
        with open(os.path.join(install, "versions", "1.21.1", "1.21.1.jar"), "wb") as f:
            f.write(jar_bytes({"assets/minecraft/lang/en_us.json": {"item.minecraft.apple": "Apple"}}))
        digest = "ab" + "0" * 38
        os.makedirs(os.path.join(install, "assets", "objects", "ab"))
        with open(os.path.join(install, "assets", "objects", "ab", digest), "w", encoding="utf-8") as f:
            json.dump({"item.minecraft.apple": "Яблоко"}, f, ensure_ascii=False)
        with open(os.path.join(install, "assets", "indexes", "17.json"), "w") as f:
            json.dump({"objects": {"minecraft/lang/ru_ru.json": {"hash": digest}}}, f)
        return base

    def test_read_from_curseforge_install(self):
        base = self._install()
        en, ru = read_vanilla_lang(base, "1.21.1", "ru_ru")
        self.assertEqual((en["item.minecraft.apple"], ru["item.minecraft.apple"]), ("Apple", "Яблоко"))
        self.assertEqual(collect_official([], "ru_ru", base, "1.21.1").terms["Apple"].ru, "Яблоко")

    def test_other_version_or_language_gives_nothing(self):
        base = self._install()
        self.assertEqual(read_vanilla_lang(base, "1.20.1", "ru_ru"), ({}, {}))
        self.assertEqual(read_vanilla_lang(base, "1.21.1", "de_de"), ({}, {}))
        self.assertEqual(read_vanilla_lang(base, "", "ru_ru"), ({}, {}))


if __name__ == "__main__":
    unittest.main()
