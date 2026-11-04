"""Fabric: lang из META-INF/jars, официальные ключи не перекрываются, NeoForge не затронут."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile

from src import mirrorer
from src.translators import Translator
from src.utils.cache import TranslationCache

ZIP_NAME = "MinecraftTranslator_ru_ru.zip"


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.sent = []

    def chat(self, messages, **kwargs):
        self.sent.append(messages[-1]["content"])
        return "ПЕР " + messages[-1]["content"]


def jar_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, data in files.items():
            z.writestr(name, data if isinstance(data, (bytes, str)) else json.dumps(data, ensure_ascii=False))
    return buffer.getvalue()


def lang(namespace, en, ru=None):
    files = {f"assets/{namespace}/lang/en_us.json": en}
    if ru is not None:
        files[f"assets/{namespace}/lang/ru_ru.json"] = ru
    return files


def make_instance(loader, jars):
    base = tempfile.mkdtemp()
    with open(os.path.join(base, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"minecraft": {"version": "1.21.1", "modLoaders": [{"id": f"{loader}-1.0", "primary": True}]}}, f)
    os.makedirs(os.path.join(base, "mods"))
    for name, data in jars.items():
        with open(os.path.join(base, "mods", name), "wb") as f:
            f.write(data)
    return base


def run(base):
    out = tempfile.mkdtemp()
    client = FakeLLM()
    translator = Translator(
        client, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
        strict=True, log=lambda s: None, target_lang="ru_ru",
    )
    logs, totals = [], []
    with contextlib.redirect_stdout(io.StringIO()):
        mirrorer.mirror_translate_dir(base, out, translator, log=logs.append, on_total=totals.append, target_lang="ru_ru")
    path = os.path.join(out, ZIP_NAME)
    pack = {}
    if os.path.exists(path):
        with zipfile.ZipFile(path) as z:
            pack = {n: json.loads(z.read(n)) for n in z.namelist() if n != "pack.mcmeta"}
    return client, pack, logs, totals


def fabric_jar(nested, own=None):
    files = {"fabric.mod.json": {"id": "x", "jars": [{"file": f"META-INF/jars/{n}"} for n in nested]}}
    for name, data in nested.items():
        files[f"META-INF/jars/{name}"] = data
    files.update(own or {})
    return jar_bytes(files)


class NestedLang(unittest.TestCase):
    def test_lang_from_nested_jar_goes_to_pack(self):
        inner = jar_bytes(lang("inner", {"k": "Inner text"}))
        _, pack, logs, _ = run(make_instance("fabric", {"outer.jar": fabric_jar({"inner.jar": inner})}))
        self.assertEqual(pack["assets/inner/lang/ru_ru.json"], {"k": "ПЕР Inner text"})
        self.assertTrue(any("outer.jar!inner.jar" in x for x in logs))

    def test_jar_with_only_nested_lang_is_counted(self):
        inner = jar_bytes(lang("inner", {"k": "Inner text"}))
        _, _, _, totals = run(make_instance("fabric", {"outer.jar": fabric_jar({"inner.jar": inner})}))
        self.assertEqual(totals, [1])

    def test_nesting_inside_nesting(self):
        deep = jar_bytes(lang("deep", {"k": "Deep text"}))
        middle = fabric_jar({"deep.jar": deep})
        _, pack, _, _ = run(make_instance("fabric", {"outer.jar": fabric_jar({"middle.jar": middle})}))
        self.assertIn("assets/deep/lang/ru_ru.json", pack)

    def test_undeclared_jar_in_meta_inf_jars_is_read(self):
        inner = jar_bytes(lang("inner", {"k": "Inner text"}))
        outer = jar_bytes({"fabric.mod.json": {"id": "x"}, "META-INF/jars/inner.jar": inner})
        _, pack, _, _ = run(make_instance("fabric", {"outer.jar": outer}))
        self.assertIn("assets/inner/lang/ru_ru.json", pack)

    def test_official_in_nested_jar_stays_out_of_pack(self):
        inner = jar_bytes(lang("inner", {"a": "Iron", "b": "Gold bar"}, {"a": "Железо"}))
        client, pack, _, _ = run(make_instance("fabric", {"outer.jar": fabric_jar({"inner.jar": inner})}))
        self.assertEqual(client.sent, ["Gold bar"])
        self.assertEqual(pack["assets/inner/lang/ru_ru.json"], {"b": "ПЕР Gold bar"})

    def test_official_in_outer_jar_blocks_machine_text_from_nested(self):
        inner = jar_bytes(lang("shared", {"a": "Iron bar"}))
        outer = fabric_jar({"inner.jar": inner}, own=lang("shared", {"a": "Iron bar", "x": "Extra text"}, {"a": "Железо"}))
        _, pack, _, _ = run(make_instance("fabric", {"outer.jar": outer}))
        self.assertEqual(pack["assets/shared/lang/ru_ru.json"], {"x": "ПЕР Extra text"})

    def test_broken_nested_jar_does_not_stop_the_rest(self):
        good = jar_bytes(lang("good", {"k": "Good text"}))
        outer = fabric_jar({"bad.jar": b"not a zip", "good.jar": good})
        _, pack, logs, _ = run(make_instance("fabric", {"outer.jar": outer}))
        self.assertIn("assets/good/lang/ru_ru.json", pack)
        self.assertFalse([x for x in logs if x.startswith("[ERR")])

    def test_jars_on_disk_do_not_change(self):
        inner = jar_bytes(lang("inner", {"k": "Inner text"}))
        base = make_instance("fabric", {"outer.jar": fabric_jar({"inner.jar": inner})})
        path = os.path.join(base, "mods", "outer.jar")
        with open(path, "rb") as f:
            before = hashlib.sha256(f.read()).hexdigest()
        run(base)
        with open(path, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), before)


class OtherLoaders(unittest.TestCase):
    def _nested_instance(self, loader):
        inner = jar_bytes(lang("inner", {"k": "Inner text"}))
        own = lang("own", {"k": "Own text"})
        return make_instance(loader, {"outer.jar": fabric_jar({"inner.jar": inner}, own=own)})

    def test_neoforge_and_forge_do_not_read_meta_inf_jars(self):
        for loader in ("neoforge", "forge"):
            client, pack, _, _ = run(self._nested_instance(loader))
            self.assertEqual(client.sent, ["Own text"], loader)
            self.assertNotIn("assets/inner/lang/ru_ru.json", pack, loader)

    def test_quilt_reads_them(self):
        _, pack, _, _ = run(self._nested_instance("quilt"))
        self.assertIn("assets/inner/lang/ru_ru.json", pack)

    def test_jarjar_is_never_read(self):
        inner = jar_bytes(lang("inner", {"k": "Inner text"}))
        outer = jar_bytes({"META-INF/jarjar/inner.jar": inner, **lang("own", {"k": "Own text"})})
        for loader in ("neoforge", "fabric"):
            client, _, _, _ = run(make_instance(loader, {"outer.jar": outer}))
            self.assertEqual(client.sent, ["Own text"], loader)

    def test_unknown_loader_reads_fabric_nesting(self):
        inner = jar_bytes(lang("inner", {"k": "Inner text"}))
        base = tempfile.mkdtemp()
        os.makedirs(os.path.join(base, "mods"))
        with open(os.path.join(base, "mods", "outer.jar"), "wb") as f:
            f.write(fabric_jar({"inner.jar": inner}))
        _, pack, _, _ = run(base)
        self.assertIn("assets/inner/lang/ru_ru.json", pack)


if __name__ == "__main__":
    unittest.main()
