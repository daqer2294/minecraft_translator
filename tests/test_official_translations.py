"""Официальный перевод не меняется, модель получает только недостающее."""
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
from src.utils.helpers import is_untranslatable, parse_lang_bytes


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.sent = []

    def chat(self, messages, **kwargs):
        user = messages[-1]["content"]
        if "JSON array" in messages[0]["content"]:
            items = json.loads(user)
            self.sent += items
            return json.dumps(["ПЕР " + x for x in items], ensure_ascii=False)
        self.sent.append(user)
        return "ПЕР " + user


def run(base):
    out = tempfile.mkdtemp()
    client = FakeLLM()
    translator = Translator(
        client, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
        strict=True, log=lambda s: None, target_lang="ru_ru",
    )
    logs = []
    with contextlib.redirect_stdout(io.StringIO()):
        mirrorer.mirror_translate_dir(base, out, translator, log=logs.append, target_lang="ru_ru")
    return client, out, logs


def make_jar(base, en, ru=None, ru_raw=None, ru_name="ru_ru.json"):
    mods = os.path.join(base, "mods")
    os.makedirs(mods, exist_ok=True)
    with zipfile.ZipFile(os.path.join(mods, "x.jar"), "w") as z:
        z.writestr("assets/x/lang/en_us.json", json.dumps(en))
        if ru is not None:
            z.writestr(f"assets/x/lang/{ru_name}", json.dumps(ru, ensure_ascii=False))
        if ru_raw is not None:
            z.writestr(f"assets/x/lang/{ru_name}", ru_raw)


def jar_output(out):
    path = os.path.join(out, "jar_lang", "x", "assets", "x", "lang", "ru_ru.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class JarOfficial(unittest.TestCase):
    EN = {"a": "Iron Ingot", "b": "Gold Ingot", "same": "Copper Ingot", "gone": "Tin Ingot"}
    RU = {"a": "Железный слиток (офиц.)", "same": "Copper Ingot", "only_ru": "Ключ только в ru"}

    def setUp(self):
        self.base = tempfile.mkdtemp()

    def test_only_missing_keys_reach_the_model(self):
        make_jar(self.base, self.EN, self.RU)
        client, _, _ = run(self.base)
        self.assertEqual(sorted(client.sent), ["Copper Ingot", "Gold Ingot", "Tin Ingot"])

    def test_official_values_are_unchanged_and_new_keys_added(self):
        make_jar(self.base, self.EN, self.RU)
        _, out, _ = run(self.base)
        self.assertEqual(jar_output(out), {
            "a": "Железный слиток (офиц.)",
            "b": "ПЕР Gold Ingot",
            "same": "ПЕР Copper Ingot",
            "gone": "ПЕР Tin Ingot",
            "only_ru": "Ключ только в ru",
        })

    def test_fully_translated_jar_writes_nothing(self):
        make_jar(self.base, {"a": "Iron Ingot"}, {"a": "Железный слиток"})
        client, out, logs = run(self.base)
        self.assertEqual(client.sent, [])
        self.assertIsNone(jar_output(out))
        self.assertTrue(any(line.startswith("[SKIP][official]") for line in logs))

    def test_official_with_trailing_comma_is_kept(self):
        make_jar(self.base, {"a": "Iron Ingot", "b": "Gold"},
                 ru_raw='{\n "a": "Железный слиток",\n}'.encode("utf-8"))
        client, out, _ = run(self.base)
        self.assertEqual(client.sent, ["Gold"])
        self.assertEqual(jar_output(out)["a"], "Железный слиток")

    def test_official_with_bom_is_kept(self):
        make_jar(self.base, {"a": "Iron Ingot"}, ru_raw=b"\xef\xbb\xbf" + '{"a": "Железный слиток"}'.encode("utf-8"))
        client, _, _ = run(self.base)
        self.assertEqual(client.sent, [])

    def test_unreadable_official_is_never_overwritten(self):
        make_jar(self.base, {"a": "Iron Ingot"}, ru_raw=b"not json at all")
        client, out, logs = run(self.base)
        self.assertEqual(client.sent, [])
        self.assertIsNone(jar_output(out))
        self.assertTrue(any("не читается" in line for line in logs))

    def test_locale_name_is_case_insensitive(self):
        make_jar(self.base, {"a": "Iron Ingot"}, {"a": "Железный слиток"}, ru_name="ru_RU.json")
        client, _, _ = run(self.base)
        self.assertEqual(client.sent, [])

    def test_untranslated_key_is_left_to_the_game_fallback(self):
        make_jar(self.base, {"a": "Iron Ingot", "url": "https://example.com"})
        _, out, _ = run(self.base)
        self.assertEqual(jar_output(out), {"a": "ПЕР Iron Ingot"})


class LooseOfficial(unittest.TestCase):
    def _write(self, name, data):
        d = os.path.join(self.base, "kubejs", "assets", "x", "lang")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def setUp(self):
        self.base = tempfile.mkdtemp()

    def test_loose_lang_merges_with_sibling(self):
        self._write("en_us.json", {"a": "Iron Ingot", "b": "Gold Ingot"})
        self._write("ru_ru.json", {"a": "Железный слиток"})
        client, out, _ = run(self.base)
        self.assertEqual(client.sent, ["Gold Ingot"])
        with open(os.path.join(out, "kubejs/assets/x/lang/ru_ru.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": "Железный слиток", "b": "ПЕР Gold Ingot"})

    def test_patchouli_page_with_translation_is_skipped(self):
        book = os.path.join(self.base, "kubejs/assets/x/patchouli_books/b")
        for locale, text in (("en_us", "English page"), ("ru_ru", "Русская страница")):
            os.makedirs(os.path.join(book, locale))
            with open(os.path.join(book, locale, "p.json"), "w", encoding="utf-8") as f:
                json.dump({"text": text}, f, ensure_ascii=False)
        client, _, _ = run(self.base)
        self.assertEqual(client.sent, [])


class FtbOfficial(unittest.TestCase):
    def _write(self, rel, text):
        path = os.path.join(self.base, "config/ftbquests/quests/lang", rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def setUp(self):
        self.base = tempfile.mkdtemp()

    def test_official_keys_survive_and_missing_are_added(self):
        self._write("en_us.snbt", '{ quest.A.title: "First" quest.B.title: "Second" quest.C.title: "Same" }')
        self._write("ru_ru.snbt", '{ quest.A.title: "Первый" quest.C.title: "Same" quest.Z.title: "Лишний" }')
        client, out, _ = run(self.base)
        self.assertEqual(sorted(client.sent), ["Same", "Second"])
        with open(os.path.join(out, "config/ftbquests/quests/lang/ru_ru.snbt"), encoding="utf-8") as f:
            text = f.read()
        for fragment in ('"Первый"', '"ПЕР Second"', '"ПЕР Same"', '"Лишний"'):
            self.assertIn(fragment, text)

    def test_fully_translated_file_writes_nothing(self):
        self._write("en_us.snbt", '{ quest.A.title: "First" }')
        self._write("ru_ru.snbt", '{ quest.A.title: "Первый" }')
        client, out, _ = run(self.base)
        self.assertEqual(client.sent, [])
        self.assertEqual(sum(len(f) for _, _, f in os.walk(out)), 0)


class Untranslatable(unittest.TestCase):
    def test_nothing_to_translate(self):
        for text in ("", "   ", "%s", "%1$s: %2$s", "{0}", "§a§l", "&6", "<red>", "123", "---",
                     "https://example.com/x", "www.example.com", "minecraft:stone",
                     "tip.tipsmod.animal_growing.text", "some_mod_id", "10s", "1.5 kW", "R"):
            self.assertTrue(is_untranslatable(text), repr(text))

    def test_real_text_is_translated(self):
        for text in ("Iron Ingot", "Seat", "Hello %s", "§aGreen", "Go to www.example.com now",
                     "Level 5", "e.g. this", "Don't", "OK then"):
            self.assertFalse(is_untranslatable(text), repr(text))

    def test_keys_are_not_sent_to_the_model(self):
        client = FakeLLM()
        tr = Translator(client, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
                        strict=True, log=lambda s: None, target_lang="ru_ru")
        self.assertEqual(tr.translate("tip.tipsmod.animal_growing.text"), "tip.tipsmod.animal_growing.text")
        self.assertEqual(tr.translate_many(["https://x.io", "Iron Ingot"]), ["https://x.io", "ПЕР Iron Ingot"])
        self.assertEqual(client.sent, ["Iron Ingot"])


class LangParser(unittest.TestCase):
    def test_bom_comma_and_garbage(self):
        self.assertEqual(parse_lang_bytes(b'\xef\xbb\xbf{"a": "b"}'), {"a": "b"})
        self.assertEqual(parse_lang_bytes(b'{"a": "b",\n}'), {"a": "b"})
        self.assertIsNone(parse_lang_bytes(b"nope"))
        self.assertIsNone(parse_lang_bytes(b"[1]"))
        self.assertIsNone(parse_lang_bytes(b"\xff\xfe"))


if __name__ == "__main__":
    unittest.main()
