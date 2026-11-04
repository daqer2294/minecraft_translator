"""Вывод: один готовый ресурспак, файлы квестов и KubeJS отдельно, оригиналы не тронуты."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
import zipfile

from src import mirrorer
from src.resource_pack import ResourcePackBuilder
from src.translators import Translator
from src.utils.cache import TranslationCache

ZIP_NAME = "MinecraftTranslator_ru_ru.zip"


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        user = messages[-1]["content"]
        if "JSON array" in messages[0]["content"]:
            return json.dumps(["ПЕР " + x for x in json.loads(user)], ensure_ascii=False)
        return "ПЕР " + user


def add_jar(base, name, files):
    mods = os.path.join(base, "mods")
    os.makedirs(mods, exist_ok=True)
    with zipfile.ZipFile(os.path.join(mods, name), "w") as z:
        for member, data in files.items():
            z.writestr(member, json.dumps(data, ensure_ascii=False))


def add_file(base, rel, data):
    path = os.path.join(base, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))


def run(base, out=None, cache=None, write=True, target_lang="ru_ru"):
    out = out or tempfile.mkdtemp()
    client = FakeLLM()
    translator = Translator(
        client, cache if cache is not None else TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
        strict=True, log=lambda s: None, target_lang=target_lang,
    )
    logs = []
    with contextlib.redirect_stdout(io.StringIO()):
        mirrorer.mirror_translate_dir(base, out, translator, log=logs.append, write=write, target_lang=target_lang)
    return client, out, logs


def tree(root):
    return sorted(
        os.path.relpath(os.path.join(r, f), root).replace("\\", "/")
        for r, _, fs in os.walk(root) for f in fs
    )


def snapshot(root):
    result = {}
    for rel in tree(root):
        with open(os.path.join(root, rel), "rb") as f:
            result[rel] = hashlib.sha256(f.read()).hexdigest()
    return result


class PackOutput(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        add_jar(self.base, "a.jar", {"assets/a/lang/en_us.json": {"a.k": "Iron Ingot"}})
        add_jar(self.base, "b.jar", {"assets/b/lang/en_us.json": {"b.k": "Gold Ingot"}})
        add_file(self.base, "resourcepacks/own/assets/own/lang/en_us.json", {"own.k": "Own pack text"})
        add_file(self.base, "kubejs/assets/kube/lang/en_us.json", {"kube.k": "Kube text"})
        add_file(self.base, "config/ftbquests/quests/lang/en_us.snbt", '{ quest.A.title: "Quest text" }')

    def test_layout(self):
        _, out, _ = run(self.base)
        self.assertEqual(tree(out), [
            ZIP_NAME,
            "config/ftbquests/quests/lang/ru_ru.snbt",
            "kubejs/assets/kube/lang/ru_ru.json",
        ])

    def test_zip_is_a_valid_pack(self):
        _, out, _ = run(self.base)
        with zipfile.ZipFile(os.path.join(out, ZIP_NAME)) as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(z.namelist()[0], "pack.mcmeta")
            meta = json.loads(z.read("pack.mcmeta"))
            self.assertEqual(meta["pack"]["pack_format"], 34)
            self.assertTrue(meta["pack"]["description"])
            self.assertEqual(json.loads(z.read("assets/a/lang/ru_ru.json")), {"a.k": "ПЕР Iron Ingot"})
            self.assertEqual(json.loads(z.read("assets/own/lang/ru_ru.json")), {"own.k": "ПЕР Own pack text"})
            self.assertFalse([n for n in z.namelist() if "kube" in n or "ftbquests" in n])

    def test_input_folder_is_not_changed(self):
        before = snapshot(self.base)
        run(self.base)
        self.assertEqual(snapshot(self.base), before)

    def test_dry_run_writes_nothing(self):
        _, out, _ = run(self.base, write=False)
        self.assertEqual(tree(out), [])

    def test_no_text_means_no_pack(self):
        empty = tempfile.mkdtemp()
        add_jar(empty, "a.jar", {"assets/a/lang/en_us.json": {"a.k": "Iron"},
                                 "assets/a/lang/ru_ru.json": {"a.k": "Железо"}})
        _, out, _ = run(empty)
        self.assertEqual(tree(out), [])

    def test_second_run_replaces_zip_without_requests(self):
        cache = TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json"))
        _, out, _ = run(self.base, cache=cache)
        with zipfile.ZipFile(os.path.join(out, ZIP_NAME)) as z:
            first = {n: z.read(n) for n in z.namelist()}
        client, _, _ = run(self.base, out=out, cache=cache)
        self.assertEqual(client.calls, 0)
        with zipfile.ZipFile(os.path.join(out, ZIP_NAME)) as z:
            self.assertEqual({n: z.read(n) for n in z.namelist()}, first)

    def test_zip_is_named_for_the_language_and_uses_its_path(self):
        _, out, _ = run(self.base, target_lang="de_de")
        with zipfile.ZipFile(os.path.join(out, "MinecraftTranslator_de_de.zip")) as z:
            self.assertIn("assets/a/lang/de_de.json", z.namelist())


class SharedNamespace(unittest.TestCase):
    def test_jars_with_one_namespace_are_merged(self):
        base = tempfile.mkdtemp()
        add_jar(base, "a.jar", {"assets/shared/lang/en_us.json": {"k1": "One text"}})
        add_jar(base, "b.jar", {"assets/shared/lang/en_us.json": {"k2": "Two text"}})
        _, out, _ = run(base)
        with zipfile.ZipFile(os.path.join(out, ZIP_NAME)) as z:
            self.assertEqual(json.loads(z.read("assets/shared/lang/ru_ru.json")),
                             {"k1": "ПЕР One text", "k2": "ПЕР Two text"})

    def test_machine_translation_never_covers_official_from_another_jar(self):
        for names in (("a.jar", "b.jar"), ("b.jar", "a.jar")):
            base = tempfile.mkdtemp()
            add_jar(base, names[0], {"assets/shared/lang/en_us.json": {"k": "Text", "x": "Extra"},
                                     "assets/shared/lang/ru_ru.json": {"k": "Официальный"}})
            add_jar(base, names[1], {"assets/shared/lang/en_us.json": {"k": "Text"}})
            _, out, _ = run(base)
            with zipfile.ZipFile(os.path.join(out, ZIP_NAME)) as z:
                self.assertEqual(json.loads(z.read("assets/shared/lang/ru_ru.json")), {"x": "ПЕР Extra"})

    def test_conflicting_official_translations_are_left_to_the_game(self):
        base = tempfile.mkdtemp()
        add_jar(base, "a.jar", {"assets/shared/lang/en_us.json": {"k": "Text", "x": "Extra"},
                                "assets/shared/lang/ru_ru.json": {"k": "Вариант А"}})
        add_jar(base, "b.jar", {"assets/shared/lang/en_us.json": {"k": "Text"},
                                "assets/shared/lang/ru_ru.json": {"k": "Вариант Б"}})
        _, out, _ = run(base)
        with zipfile.ZipFile(os.path.join(out, ZIP_NAME)) as z:
            self.assertEqual(json.loads(z.read("assets/shared/lang/ru_ru.json")), {"x": "ПЕР Extra"})


class FailedWrite(unittest.TestCase):
    def test_failure_keeps_previous_zip_and_leaves_no_temp_file(self):
        out = tempfile.mkdtemp()
        pack = ResourcePackBuilder(out, "ru_ru")
        pack.add_lang("assets/a/lang/ru_ru.json", {"k": "старый"})
        path = pack.finish()
        broken = ResourcePackBuilder(out, "ru_ru")
        broken.add_json("assets/a/x.json", {"bad": threading.Lock()})
        with self.assertRaises(TypeError):
            broken.finish()
        with zipfile.ZipFile(path) as z:
            self.assertEqual(json.loads(z.read("assets/a/lang/ru_ru.json")), {"k": "старый"})
        self.assertEqual(os.listdir(out), [os.path.basename(path)])


if __name__ == "__main__":
    unittest.main()
