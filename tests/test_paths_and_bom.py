"""Результат не зависит от рабочей папки (из Finder она равна «/»), BOM не ломает чтение."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

from src import mirrorer
from src.translators import Translator
from src.utils.cache import TranslationCache

BOM = b"\xef\xbb\xbf"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def write_bom(base, rel, text):
    path = os.path.join(base, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(BOM + text.encode("utf-8"))


def run(base, write=True):
    client = FakeLLM()
    translator = Translator(
        client, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
        strict=True, log=lambda s: None, target_lang="ru_ru",
    )
    logs = []
    with contextlib.redirect_stdout(io.StringIO()):
        mirrorer.mirror_translate_dir(base, tempfile.mkdtemp(), translator, log=logs.append, write=write, target_lang="ru_ru")
    return client, [x for x in logs if x.startswith(("[ERR", "[WARN", "[LWRN")) and "[pack-info]" not in x]


class BomFiles(unittest.TestCase):
    CASES = {
        "lang": ("kubejs/assets/x/lang/en_us.json", '{"k": "Iron Ingot"}', "Iron Ingot"),
        "patchouli": ("kubejs/assets/x/patchouli_books/b/en_us/p.json", '{"text": "Page text here"}', "Page text here"),
        "tips": ("kubejs/assets/x/tips/t.json", '{"text": "Tip text here"}', "Tip text here"),
        "snbt": ("config/ftbquests/quests/lang/en_us.snbt", '{ quest.A.title: "Quest text" }', "Quest text"),
    }

    def test_each_file_type_with_bom_is_translated(self):
        for name, (rel, text, expected) in self.CASES.items():
            with self.subTest(name):
                base = tempfile.mkdtemp()
                write_bom(base, rel, text)
                client, problems = run(base)
                self.assertEqual(problems, [])
                self.assertIn(expected, client.sent)

    def test_kubejs_script_with_bom_is_read_without_errors(self):
        base = tempfile.mkdtemp()
        write_bom(base, "kubejs/client_scripts/a.js", 'Text.of("Script text")')
        _, problems = run(base)
        self.assertEqual(problems, [])

    def test_dry_run_reads_bom_too(self):
        base = tempfile.mkdtemp()
        for rel, text, _ in (self.CASES["lang"], self.CASES["patchouli"], self.CASES["tips"]):
            write_bom(base, rel, text)
        _, problems = run(base, write=False)
        self.assertEqual(problems, [])


class UnchangedScripts(unittest.TestCase):
    def test_script_without_text_is_not_copied(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        path = os.path.join(base, "kubejs", "server_scripts", "a.js")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            f.write("ServerEvents.recipes(event => { event.remove({ id: 'a:b' }) })")
        translator = Translator(
            FakeLLM(), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
            strict=True, log=lambda s: None, target_lang="ru_ru",
        )
        ticks, logs = [], []
        with contextlib.redirect_stdout(io.StringIO()):
            mirrorer.mirror_translate_dir(
                base, out, translator, log=logs.append,
                on_tick=lambda *a: ticks.append(a), target_lang="ru_ru",
            )
        self.assertEqual(os.listdir(out), [])
        self.assertEqual(ticks, [(1, 0, 0, 1)])
        self.assertTrue(any(x.startswith("[SKIP][unchanged]") for x in logs))


class RunFromRoot(unittest.TestCase):
    """Отдельный процесс: пути считаются при импорте config."""

    def _probe(self, code, **env):
        home = tempfile.mkdtemp()
        environment = {k: v for k, v in os.environ.items() if k not in ("TRANSLATIONS_CACHE", "MC_TRANSLATOR_HOME")}
        environment.update(HOME=home, PYTHONPATH=REPO, **env)
        result = subprocess.run([sys.executable, "-c", code], cwd="/", env=environment, capture_output=True, text=True, check=True)
        return json.loads(result.stdout.strip().splitlines()[-1]), home

    def test_default_paths_are_absolute_and_under_home(self):
        code = (
            "import json;from src import config;"
            "print(json.dumps([config.DEFAULT_CACHE_PATH, config.DEFAULT_OUTPUT_DIR]))"
        )
        (cache, output), home = self._probe(code)
        self.assertEqual(cache, os.path.join(home, ".minecraft_translator", "translations_cache.json"))
        self.assertEqual(output, os.path.join(home, "MinecraftTranslator"))

    def test_env_override_still_works(self):
        (cache, _), _ = self._probe(
            "import json;from src import config;print(json.dumps([config.DEFAULT_CACHE_PATH, 0]))",
            TRANSLATIONS_CACHE="/tmp/custom.json",
        )
        self.assertEqual(cache, "/tmp/custom.json")

    def test_relative_output_resolves_against_home_not_cwd(self):
        code = (
            "import json;from src.gui.api import Api;"
            "print(json.dumps([Api.resolve_output('out'), Api.resolve_output('/abs/out'), Api.resolve_output('')]))"
        )
        (relative, absolute, empty), home = self._probe(code)
        self.assertEqual(relative, os.path.join(home, "out"))
        self.assertEqual(absolute, "/abs/out")
        self.assertEqual(empty, os.path.join(home, "MinecraftTranslator"))


class DeadCodeIsGone(unittest.TestCase):
    def test_removed_modules_stay_removed(self):
        for name in ("scanner.py", "gui_main_legacy.py"):
            self.assertFalse(os.path.exists(os.path.join(REPO, "src", name)), name)

    def test_app_entry_points_import(self):
        for module in ("src.gui.api", "src.mirrorer", "src.translators"):
            subprocess.run([sys.executable, "-c", f"import {module}"], cwd=REPO, check=True)


if __name__ == "__main__":
    unittest.main()
