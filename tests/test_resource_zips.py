"""Паки ресурсов вне mods: en_us из zip идёт в наш пак, чужой готовый перевод не перекрывается."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
import zipfile

from src import mirrorer
from src.translators import Translator
from src.utils.cache import TranslationCache

TIP_KEY = "tip.cobblemontips.ancient_poke_balls.text"
TIP = (
    "Tumblestones can be found in the ruins of the world. These funny little rocks can be used to craft replicas of "
    "Ancient Pokémon Balls."
)


class Model:
    name = "m"

    def __init__(self, reply=None):
        self.reply = reply or (lambda text: "ПЕР " + text)
        self.seen = []

    def chat(self, messages, **kwargs):
        text = messages[-1]["content"]
        if "JSON array" in messages[0]["content"]:
            items = json.loads(text)
            self.seen += items
            return json.dumps([self.reply(x) for x in items], ensure_ascii=False)
        self.seen.append(text)
        return self.reply(text)


def make_zip(base, rel, files):
    path = os.path.join(base, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))


def make_jar(base, name, files):
    make_zip(base, f"mods/{name}", files)


def run(base, model=None, strong=None):
    model, out, logs = model or Model(), tempfile.mkdtemp(), []
    tr = Translator(model, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                    log=lambda s: None, target_lang="ru_ru")
    tr.max_attempts = 1
    with contextlib.redirect_stdout(io.StringIO()):
        mirrorer.mirror_translate_dir(
            base, out, tr, log=logs.append, target_lang="ru_ru",
            strong_client=(lambda: (strong, "")) if strong else None,
        )
    pack = {}
    for name in os.listdir(out):
        if name.endswith(".zip"):
            with zipfile.ZipFile(os.path.join(out, name)) as z:
                for member in z.namelist():
                    if member.endswith("/ru_ru.json"):
                        pack[member] = json.loads(z.read(member))
    return model, pack, logs


class Folders(unittest.TestCase):
    def test_cobblemon_tips_from_openloader_reach_the_pack(self):
        base = tempfile.mkdtemp()
        make_zip(base, "config/openloader/resources/Cobblemon-Tips-1.0.0.zip", {
            "pack.mcmeta": {"pack": {"pack_format": 34, "description": "tips"}},
            "assets/cobblemontips/tips/ancient_poke_balls.json": {"type": "tipsmod:simple", "text": {"translate": TIP_KEY}},
            "assets/cobblemontips/lang/en_us.json": {TIP_KEY: TIP},
        })
        model, pack, _ = run(base)
        self.assertEqual(pack, {"assets/cobblemontips/lang/ru_ru.json": {TIP_KEY: "ПЕР " + TIP}})
        self.assertIn(TIP, model.seen)

    def test_resourcepacks_and_openloader_packs_are_read(self):
        for rel in ("resourcepacks/a.zip", "resourcepacks/A.ZIP", "config/openloader/packs/a.zip"):
            base = tempfile.mkdtemp()
            make_zip(base, rel, {"assets/a/lang/en_us.json": {"k": "Some text"}})
            _, pack, _ = run(base)
            self.assertEqual(pack, {"assets/a/lang/ru_ru.json": {"k": "ПЕР Some text"}}, rel)

    def test_other_folders_are_not_read(self):
        for rel in ("config/resourcepacks/a.zip", "saves/w/resources.zip", "a.zip", "resourcepacks/sub/a.zip", "showdown/a.zip"):
            base = tempfile.mkdtemp()
            make_zip(base, rel, {"assets/a/lang/en_us.json": {"k": "Some text"}})
            make_jar(base, "m.jar", {"assets/m/lang/en_us.json": {"j": "Mod text"}})
            model, pack, _ = run(base)
            self.assertEqual(pack, {"assets/m/lang/ru_ru.json": {"j": "ПЕР Mod text"}}, rel)
            self.assertNotIn("Some text", model.seen, rel)

    def test_broken_zip_does_not_stop_the_run(self):
        base = tempfile.mkdtemp()
        os.makedirs(os.path.join(base, "resourcepacks"))
        with open(os.path.join(base, "resourcepacks", "broken.zip"), "wb") as f:
            f.write(b"not a zip")
        make_zip(base, "resourcepacks/good.zip", {"assets/a/lang/en_us.json": {"k": "Some text"}})
        _, pack, _ = run(base)
        self.assertEqual(pack, {"assets/a/lang/ru_ru.json": {"k": "ПЕР Some text"}})


class Official(unittest.TestCase):
    def test_translation_in_the_same_zip_stays(self):
        base = tempfile.mkdtemp()
        make_zip(base, "resourcepacks/a.zip", {
            "assets/a/lang/en_us.json": {"done": "It is ready", "new": "A new line"},
            "assets/a/lang/ru_ru.json": {"done": "Готово"},
        })
        model, pack, _ = run(base)
        self.assertEqual(pack, {"assets/a/lang/ru_ru.json": {"new": "ПЕР A new line"}})
        self.assertNotIn("It is ready", model.seen)

    def test_translation_in_a_mod_is_not_covered(self):
        base = tempfile.mkdtemp()
        make_jar(base, "m.jar", {
            "assets/m/lang/en_us.json": {"done": "It is ready"},
            "assets/m/lang/ru_ru.json": {"done": "Готово"},
        })
        make_zip(base, "resourcepacks/a.zip", {"assets/m/lang/en_us.json": {"done": "It is done", "new": "A new line"}})
        _, pack, _ = run(base)
        self.assertEqual(pack, {"assets/m/lang/ru_ru.json": {"new": "ПЕР A new line"}})

    def test_game_keys_are_not_covered(self):
        base = tempfile.mkdtemp()
        make_zip(base, "resourcepacks/a.zip", {
            "assets/minecraft/lang/en_us.json": {"menu.returnToGame": "Return to Complex Gaming"},
            "assets/a/lang/en_us.json": {"k": "Some text"},
        })
        model, pack, _ = run(base)
        self.assertEqual(pack, {"assets/a/lang/ru_ru.json": {"k": "ПЕР Some text"}})
        self.assertNotIn("Return to Complex Gaming", model.seen)

    def test_game_keys_from_a_mod_work_as_before(self):
        base = tempfile.mkdtemp()
        make_jar(base, "m.jar", {"assets/minecraft/lang/en_us.json": {"item.x": "Some item"}})
        _, pack, _ = run(base)
        self.assertEqual(pack, {"assets/minecraft/lang/ru_ru.json": {"item.x": "ПЕР Some item"}})

    def test_our_own_pack_has_no_en_us_and_is_ignored(self):
        base = tempfile.mkdtemp()
        make_zip(base, "resourcepacks/MinecraftTranslator_ru_ru.zip", {"assets/a/lang/ru_ru.json": {"k": "Текст"}})
        model, pack, logs = run(base)
        self.assertEqual(pack, {})
        self.assertEqual(model.seen, [])


class SecondPass(unittest.TestCase):
    def test_strong_model_fixes_a_string_from_a_zip(self):
        base = tempfile.mkdtemp()
        make_zip(base, "resourcepacks/a.zip", {"assets/a/lang/en_us.json": {"k": "Some text", "j": "Other line"}})
        main = Model(lambda text: "Плохой текст[ru]" if text == "Some text" else "ПЕР " + text)
        strong = Model(lambda text: "Хороший текст")
        _, pack, _ = run(base, main, strong)
        self.assertEqual(pack, {"assets/a/lang/ru_ru.json": {"k": "Хороший текст", "j": "ПЕР Other line"}})


if __name__ == "__main__":
    unittest.main()
