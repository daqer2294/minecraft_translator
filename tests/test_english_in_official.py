"""Английский текст в ru_ru.json мода это не перевод. Значения мода Cobblemon взяты дословно."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
import zipfile

from src import mirrorer
from src.processors.lang_json import (
    has_official_translation,
    is_english_left,
    keys_to_translate,
    merge_lang,
    source_text,
)
from src.translators import Translator
from src.utils.cache import TranslationCache

BULBASAUR_KEY = "cobblemon.species.bulbasaur.desc"
BULBASAUR_EN = (
    "Bulbasaur can be seen napping in bright sunlight. There is a seed on its back. By soaking up the sun's rays, "
    "the seed grows progressively larger."
)
BULBASAUR_RU = "While it is young, it uses the nutrients that are stored in the seed on its back in order to grow."

# Настоящий перевод, значения без букв и одно слово: как было, официальные.
OFFICIAL = {
    "cobblemon.species.bulbasaur.name": ("Bulbasaur", "Бульбазавр"),
    "a.sentence": ("It is young.", "Он ещё молод."),
    "a.mixed_term": ("Open the Pokédex now", "Открой Pokédex"),
    "a.brand": ("Mod Menu settings", "Настройки Mod Menu"),
    "a.percent": ("Chance: %s", "Шанс: %s"),
    "a.format": ("§aReady", "§aГотово"),
    "a.newline": ("One\nTwo", "Один\nДва"),
    "a.ukr_letter": ("Yes and no", "Так і ні"),
    "a.numbers": ("One of two", "1/2"),
    "a.symbols": ("Plus or minus", "+ / -"),
    "a.percent_only": ("Value here", "%s"),
    "a.digits": ("Twenty five", "25"),
    "a.arrow": ("Go next", "->"),
    "a.dots": ("Wait a bit", "..."),
    "os.windows": ("Is OS Windows", "Windows"),
    "os.macos": ("Is OS macOS", "macOS"),
    "os.linux": ("Is OS Linux", "Linux"),
    "skin.vanilla": ("Vanilla", "Mojang"),
    "skin.capes": ("minecraftcapes.net", "MinecraftCapes"),
    "one.word.accent": ("Pokémon team", "Pokémon"),
    "one.word.placeholder": ("Waypoints: %s", "Waypoints: "),
}
ENGLISH = {
    BULBASAUR_KEY: (BULBASAUR_EN, BULBASAUR_RU),
    "cobblemon.move.icepunch.desc": (
        "The target is attacked with an icy punch. This may also leave the target frozen.",
        "The target is punched with an icy fist. This may also leave the target frozen.",
    ),
    "jm.waypoint.delete.label": ("Delete", "Delete Waypoint"),
    "fancymenu.shortcut": ("CTRL + ALT + R", "CTRL + ALT + C"),
}


class Rule(unittest.TestCase):
    def test_bulbasaur_is_not_official(self):
        en, official = {BULBASAUR_KEY: BULBASAUR_EN}, {BULBASAUR_KEY: BULBASAUR_RU}
        self.assertFalse(has_official_translation(BULBASAUR_KEY, en, official, "ru_ru"))
        self.assertEqual(keys_to_translate(en, official, "ru_ru"), [BULBASAUR_KEY])

    def test_english_values_go_to_the_queue(self):
        for key, (en, value) in ENGLISH.items():
            self.assertTrue(is_english_left(value, "ru_ru"), key)
            self.assertFalse(has_official_translation(key, {key: en}, {key: value}, "ru_ru"), key)

    def test_real_translations_and_short_values_stay_official(self):
        self.assertGreaterEqual(len(OFFICIAL), 20)
        for key, (en, value) in OFFICIAL.items():
            self.assertTrue(has_official_translation(key, {key: en}, {key: value}, "ru_ru"), key)
            self.assertEqual(keys_to_translate({key: en}, {key: value}, "ru_ru"), [], key)

    def test_official_values_are_kept_byte_for_byte(self):
        en = {key: pair[0] for key, pair in OFFICIAL.items()}
        official = {key: pair[1] for key, pair in OFFICIAL.items()}
        self.assertEqual(merge_lang(en, official, {}, "ru_ru"), official)

    def test_only_cyrillic_targets(self):
        for lang in ("de_de", "fr_fr", "pt_br", "zh_cn", "ja_jp", "sr_cs", ""):
            self.assertFalse(is_english_left(BULBASAUR_RU, lang), lang)
            self.assertTrue(
                has_official_translation(BULBASAUR_KEY, {BULBASAUR_KEY: BULBASAUR_EN}, {BULBASAUR_KEY: BULBASAUR_RU}, lang)
            )
        for lang in ("ru_ru", "RU_RU", "uk_ua", "be_by", "bg_bg", "sr_sp"):
            self.assertTrue(is_english_left(BULBASAUR_RU, lang), lang)

    def test_other_alphabets_are_not_english(self):
        self.assertFalse(is_english_left("中文 テキスト 文字", "ru_ru"))
        self.assertFalse(is_english_left("Some text 中文", "ru_ru"))

    def test_value_equal_to_en_us_still_goes_to_the_queue(self):
        en = {"k": "Same text here"}
        self.assertEqual(keys_to_translate(en, {"k": "Same text here"}, "ru_ru"), ["k"])


class Source(unittest.TestCase):
    def test_text_from_the_official_file_is_translated(self):
        self.assertEqual(
            source_text(BULBASAUR_KEY, {BULBASAUR_KEY: BULBASAUR_EN}, {BULBASAUR_KEY: BULBASAUR_RU}, "ru_ru"),
            BULBASAUR_RU,
        )

    def test_other_markup_means_en_us(self):
        en, official = {"k": "Teleport to {name} at {x}"}, {"k": "Teleport to the point at {x}"}
        self.assertEqual(source_text("k", en, official, "ru_ru"), en["k"])

    def test_journeymap_uses_en_us(self):
        for key, en, value in (
            ("jm.waypoint.disable_waypoints", "Disable All", "Disable only the current visible Waypoints in the list."),
            ("jm.common.waypoint.select_all", "All", "Selects all waypoints"),
            ("key.journeymap.toggle_waypoints", "Toggle All Waypoints", "Toggle all Waypoints"),
        ):
            self.assertEqual(source_text(key, {key: en}, {key: value}, "ru_ru"), en)
            self.assertEqual(keys_to_translate({key: en}, {key: value}, "ru_ru"), [key])

    def test_journeymap_russian_value_stays_official(self):
        key = "jm.common.options"
        self.assertEqual(keys_to_translate({key: "Options"}, {key: "Настройки"}, "ru_ru"), [])

    def test_other_mods_keep_the_official_english(self):
        key = "jmod.other.label"
        self.assertEqual(source_text(key, {key: "Delete it"}, {key: "Delete this one"}, "ru_ru"), "Delete this one")

    def test_key_without_official_value_uses_en_us(self):
        self.assertEqual(source_text("k", {"k": "Some text"}, {}, "ru_ru"), "Some text")


class Model:
    name = "m"

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.seen = []

    def chat(self, messages, **kwargs):
        text = messages[-1]["content"]
        self.seen.append(text)
        return self.answers.get(text, text)


class Pipeline(unittest.TestCase):
    def _run(self, en, official, answers):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        os.makedirs(os.path.join(base, "mods"))
        with zipfile.ZipFile(os.path.join(base, "mods", "m.jar"), "w") as z:
            z.writestr("assets/m/lang/en_us.json", json.dumps(en, ensure_ascii=False))
            z.writestr("assets/m/lang/ru_ru.json", json.dumps(official, ensure_ascii=False))
        model = Model(answers)
        tr = Translator(model, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang="ru_ru")
        tr.max_attempts = 1
        with contextlib.redirect_stdout(io.StringIO()):
            mirrorer.mirror_translate_dir(base, out, tr, log=lambda s: None, target_lang="ru_ru")
        pack = {}
        for name in os.listdir(out):
            if name.endswith(".zip"):
                with zipfile.ZipFile(os.path.join(out, name)) as z:
                    pack = json.loads(z.read("assets/m/lang/ru_ru.json"))
        return model, pack

    def test_english_value_is_translated_and_russian_is_not_touched(self):
        model, pack = self._run(
            {BULBASAUR_KEY: BULBASAUR_EN, "ok": "It is young.", "new": "A new line"},
            {BULBASAUR_KEY: BULBASAUR_RU, "ok": "Он ещё молод."},
            {BULBASAUR_RU: "Пока он молод, он растёт за счёт семени на спине.", "A new line": "Новая строка"},
        )
        self.assertEqual(pack, {BULBASAUR_KEY: "Пока он молод, он растёт за счёт семени на спине.", "new": "Новая строка"})
        self.assertIn(BULBASAUR_RU, model.seen)
        self.assertNotIn(BULBASAUR_EN, model.seen)
        self.assertNotIn("It is young.", model.seen)

    def test_answer_equal_to_the_english_value_stays_out_of_the_pack(self):
        _, pack = self._run(
            {BULBASAUR_KEY: BULBASAUR_EN, "new": "A new line"},
            {BULBASAUR_KEY: BULBASAUR_RU},
            {"A new line": "Новая строка"},
        )
        self.assertEqual(pack, {"new": "Новая строка"})

    def test_russian_value_in_another_jar_is_not_covered(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        os.makedirs(os.path.join(base, "mods"))
        with zipfile.ZipFile(os.path.join(base, "mods", "a.jar"), "w") as z:
            z.writestr("assets/m/lang/en_us.json", json.dumps({"k": "Some long text", "new": "A new line"}))
            z.writestr("assets/m/lang/ru_ru.json", json.dumps({"k": "Другое английское"}, ensure_ascii=False))
        with zipfile.ZipFile(os.path.join(base, "mods", "b.jar"), "w") as z:
            z.writestr("assets/m/lang/en_us.json", json.dumps({"k": "Some long text"}))
            z.writestr("assets/m/lang/ru_ru.json", json.dumps({"k": "Some other english"}))
        tr = Translator(Model({"A new line": "Новая строка", "Some other english": "Машинный перевод"}),
                        TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang="ru_ru")
        tr.max_attempts = 1
        with contextlib.redirect_stdout(io.StringIO()):
            mirrorer.mirror_translate_dir(base, out, tr, log=lambda s: None, target_lang="ru_ru")
        name = [n for n in os.listdir(out) if n.endswith(".zip")][0]
        with zipfile.ZipFile(os.path.join(out, name)) as z:
            self.assertEqual(json.loads(z.read("assets/m/lang/ru_ru.json")), {"new": "Новая строка"})


if __name__ == "__main__":
    unittest.main()
