"""Версия и загрузчик из метаданных сборки, формат ресурспака по версии."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
import zipfile

from src import mirrorer
from src.modpack_info import RESOURCE_PACK_FORMATS, detect_modpack
from src.resource_pack import ResourcePackBuilder
from src.translators import Translator
from src.utils.cache import TranslationCache


def make_pack(manifest=None, instance=None, raw_manifest=None):
    base = tempfile.mkdtemp()
    if manifest is not None:
        with open(os.path.join(base, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)
    if raw_manifest is not None:
        with open(os.path.join(base, "manifest.json"), "wb") as f:
            f.write(raw_manifest)
    if instance is not None:
        with open(os.path.join(base, "minecraftinstance.json"), "w", encoding="utf-8") as f:
            json.dump(instance, f)
    return base


NEOFORGE_MANIFEST = {"minecraft": {"version": "1.21.1", "modLoaders": [{"id": "neoforge-21.1.265", "primary": True}]}}
FABRIC_INSTANCE = {"gameVersion": "1.21.1", "baseModLoader": {"name": "fabric-0.18.1-1.21.1", "minecraftVersion": "1.21.1"}}


class Detection(unittest.TestCase):
    def test_manifest_only(self):
        info = detect_modpack(make_pack(manifest=NEOFORGE_MANIFEST))
        self.assertEqual((info.minecraft, info.loader, info.loader_version), ("1.21.1", "neoforge", "21.1.265"))
        self.assertEqual(info.source, "manifest.json")

    def test_instance_only_with_fabric_name_format(self):
        info = detect_modpack(make_pack(instance=FABRIC_INSTANCE))
        self.assertEqual((info.minecraft, info.loader, info.loader_version), ("1.21.1", "fabric", "0.18.1"))

    def test_conflict_prefers_instance_and_warns(self):
        info = detect_modpack(make_pack(manifest=NEOFORGE_MANIFEST, instance=FABRIC_INSTANCE))
        self.assertEqual(info.loader, "fabric")
        self.assertTrue(any("расходятся" in w for w in info.warnings))

    def test_agreement_gives_no_warning(self):
        instance = {"baseModLoader": {"name": "neoforge-21.1.265", "minecraftVersion": "1.21.1"}}
        self.assertEqual(detect_modpack(make_pack(manifest=NEOFORGE_MANIFEST, instance=instance)).warnings, [])

    def test_bom_is_accepted(self):
        raw = b"\xef\xbb\xbf" + json.dumps(NEOFORGE_MANIFEST).encode()
        self.assertEqual(detect_modpack(make_pack(raw_manifest=raw)).loader, "neoforge")

    def test_broken_metadata_falls_back_with_warning(self):
        info = detect_modpack(make_pack(raw_manifest=b"not json"))
        self.assertIsNone(info.minecraft)
        self.assertEqual(info.pack_format, 34)
        self.assertTrue(info.warnings)

    def test_subfolder_finds_metadata_above(self):
        base = make_pack(manifest=NEOFORGE_MANIFEST)
        os.makedirs(os.path.join(base, "config", "ftbquests"))
        self.assertEqual(detect_modpack(os.path.join(base, "config", "ftbquests")).loader, "neoforge")


class PackFormats(unittest.TestCase):
    def test_every_release_from_the_sources(self):
        # minecraft.wiki/w/Pack_format и misode/mcmeta, проверено 2026-11-04
        expected = {
            "1.20.1": 15, "1.21": 34, "1.21.1": 34, "1.21.2": 42, "1.21.3": 42, "1.21.4": 46, "1.21.5": 55,
            "1.21.6": 63, "1.21.7": 64, "1.21.8": 64, "1.21.9": 69, "1.21.10": 69, "1.21.11": 75,
            "26.1": 84, "26.2": 88, "26.3": 97,
        }
        for version, number in expected.items():
            self.assertEqual(RESOURCE_PACK_FORMATS[version], number, version)

    def test_unknown_version_warns(self):
        info = detect_modpack(make_pack(manifest={"minecraft": {"version": "99.9"}}))
        self.assertEqual(info.pack_format, 34)
        self.assertTrue(any("неизвестен" in w for w in info.warnings))


class Mcmeta(unittest.TestCase):
    def _meta(self, number):
        out = tempfile.mkdtemp()
        pack = ResourcePackBuilder(out, "ru_ru", pack_format=number)
        pack.add_lang("assets/a/lang/ru_ru.json", {"k": "v"})
        with zipfile.ZipFile(pack.finish()) as z:
            return json.loads(z.read("pack.mcmeta"))["pack"]

    def test_old_format_uses_pack_format(self):
        meta = self._meta(34)
        self.assertEqual(meta["pack_format"], 34)
        self.assertNotIn("min_format", meta)

    def test_format_65_and_newer_uses_min_and_max(self):
        for number in (69, 75, 97):
            meta = self._meta(number)
            self.assertEqual((meta["min_format"], meta["max_format"]), (number, number))
            self.assertNotIn("pack_format", meta)


class EndToEnd(unittest.TestCase):
    def _run(self, base):
        os.makedirs(os.path.join(base, "mods"), exist_ok=True)
        with zipfile.ZipFile(os.path.join(base, "mods", "a.jar"), "w") as z:
            z.writestr("assets/a/lang/en_us.json", json.dumps({"k": "Iron Ingot"}))

        class Fake:
            name = "f"

            def chat(self, m, **k):
                return "ПЕР " + m[-1]["content"]

        out = tempfile.mkdtemp()
        logs = []
        tr = Translator(Fake(), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang="ru_ru")
        with contextlib.redirect_stdout(io.StringIO()):
            mirrorer.mirror_translate_dir(base, out, tr, log=logs.append, target_lang="ru_ru")
        with zipfile.ZipFile(os.path.join(out, "MinecraftTranslator_ru_ru.zip")) as z:
            return json.loads(z.read("pack.mcmeta"))["pack"], logs

    def test_pack_format_follows_the_modpack_version(self):
        meta, logs = self._run(make_pack(manifest={"minecraft": {"version": "1.20.1", "modLoaders": [{"id": "forge-47.4.0"}]}}))
        self.assertEqual(meta["pack_format"], 15)
        self.assertTrue(any(x.startswith("[PACK-INFO]") and "1.20.1" in x and "Forge" in x for x in logs))

    def test_new_version_gets_min_max(self):
        meta, _ = self._run(make_pack(manifest={"minecraft": {"version": "1.21.11"}}))
        self.assertEqual((meta["min_format"], meta["max_format"]), (75, 75))

    def test_no_metadata_warns_and_uses_default(self):
        meta, logs = self._run(tempfile.mkdtemp())
        self.assertEqual(meta["pack_format"], 34)
        self.assertTrue(any(x.startswith("[WARN][pack-info]") for x in logs))


if __name__ == "__main__":
    unittest.main()
