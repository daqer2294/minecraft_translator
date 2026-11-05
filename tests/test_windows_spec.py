"""Спецификация Windows-сборки: версия в свойствах exe, без UPX."""
from __future__ import annotations

import ast
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Spec(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "MinecraftTranslator-win.spec"), encoding="utf-8") as f:
            self.src = f.read()
        ast.parse(self.src)

    def test_spec_reads_the_app_version(self):
        from src.version import __version__
        self.assertIn('os.path.join("src", "version.py")', self.src)
        self.assertIn('StringStruct("OriginalFilename", "MinecraftTranslator.exe")', self.src)
        self.assertIn("version=version_info", self.src)
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    # Модуль версий PyInstaller требует pefile: он есть на Windows, на Mac его нет.
    @unittest.skipUnless(importlib.util.find_spec("pefile"), "pefile ставится только на Windows")
    def test_version_resource_from_the_app_version(self):
        ns = {}
        os.chdir(ROOT)
        exec(self.src.split("block_cipher = None")[0], ns)
        from src.version import __version__
        self.assertEqual(ns["VERSION"], __version__)
        text = str(ns["version_info"])
        self.assertIn("MinecraftTranslator.exe", text)
        self.assertIn(__version__, text)

    def test_folder_build_not_onefile(self):
        tree = ast.parse(self.src)
        calls = {n.func.id: n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertIn("COLLECT", calls)
        exe = calls["EXE"]
        self.assertTrue(any(k.arg == "exclude_binaries" and k.value.value is True for k in exe.keywords))
        self.assertNotIn("a.binaries", [ast.unparse(a) for a in exe.args])

    def test_ci_packs_the_folder(self):
        with open(os.path.join(ROOT, ".github", "workflows", "build.yml"), encoding="utf-8") as f:
            ci = f.read()
        self.assertIn("Compress-Archive -Path dist/MinecraftTranslator -DestinationPath", ci)
        self.assertNotIn("Copy-Item dist/MinecraftTranslator.exe", ci)

    def test_upx_is_off(self):
        self.assertIn("upx=False", self.src)
        self.assertNotIn("upx=True", self.src)


if __name__ == "__main__":
    unittest.main()
