"""FTB Quests: перевод идёт только из en_us и пишется в целевую локаль."""
from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest

from src import mirrorer
from src.translators import Translator
from src.utils.cache import TranslationCache


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.sent = []

    def chat(self, messages, **kwargs):
        self.sent.append(messages[-1]["content"])
        return "ПЕР " + messages[-1]["content"]


FILES = {
    "lang/en_us.snbt": '{ chapter.A.title: "English flat" }',
    "lang/en_us/chapter.snbt": '{ chapter.B.title: "English split" }',
    "lang/en_us/chapters/one.snbt": '{ quest.C.title: "English chapter" }',
    "lang/de_de.snbt": '{ chapter.A.title: "Deutscher Text" }',
    "lang/pt_br/chapter.snbt": '{ chapter.B.title: "Texto mixed English" }',
    "lang/ru_ru/chapter.snbt": '{ chapter.B.title: "Готовый текст Mixed" }',
    "lang/ru_ru/chapters/one.snbt": '{ quest.C.title: "English chapter" }',
    "lang/ZH_CN.snbt": '{ chapter.A.title: "Upper case Locale" }',
    "chapters/one.snbt": '{ title: "Inline chapter title" }',
}


def write_pack(base):
    quests = os.path.join(base, "config", "ftbquests", "quests")
    for rel, text in FILES.items():
        path = os.path.join(quests, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return quests


def run(base, target_lang="ru_ru", out=None):
    out = out or tempfile.mkdtemp()
    client = FakeLLM()
    translator = Translator(
        client, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
        strict=True, log=lambda s: None, target_lang=target_lang,
    )
    totals, logs = [], []
    with contextlib.redirect_stdout(io.StringIO()):
        mirrorer.mirror_translate_dir(
            base, out, translator, log=logs.append,
            on_total=totals.append, target_lang=target_lang,
        )
    written = sorted(
        os.path.relpath(os.path.join(r, f), out).replace("\\", "/")
        for r, _, fs in os.walk(out) for f in fs
    )
    return client, totals, written, logs, out


class FtbLocales(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.quests = write_pack(self.base)
        self.prefix = "config/ftbquests/quests/"

    def test_only_english_and_inline_text_reach_the_model(self):
        client, totals, _, _, _ = run(self.base)
        self.assertEqual(
            sorted(client.sent),
            ["English chapter", "English flat", "Inline chapter title"],
        )
        self.assertEqual(totals, [4])

    def test_output_goes_to_target_locale_and_never_to_en_us(self):
        _, _, written, _, _ = run(self.base)
        self.assertEqual(written, sorted(self.prefix + p for p in (
            "lang/ru_ru.snbt", "lang/ru_ru/chapters/one.snbt", "chapters/one.snbt",
        )))

    def test_translation_lands_in_the_renamed_file(self):
        _, _, _, _, out = run(self.base)
        with open(os.path.join(out, self.prefix, "lang/ru_ru.snbt"), encoding="utf-8") as f:
            self.assertIn("ПЕР English flat", f.read())

    def test_other_target_language_renames_and_uses_english_source(self):
        client, _, written, _, _ = run(self.base, target_lang="fr_fr")
        self.assertIn(self.prefix + "lang/fr_fr.snbt", written)
        self.assertIn("English flat", client.sent)
        self.assertNotIn("Deutscher Text", client.sent)

    def test_target_language_with_its_own_file_keeps_it(self):
        client, _, written, _, _ = run(self.base, target_lang="de_de")
        self.assertNotIn(self.prefix + "lang/de_de.snbt", written)
        self.assertNotIn("English flat", client.sent)
        self.assertIn(self.prefix + "lang/de_de/chapter.snbt", written)

    def test_rerun_skips_finished_files(self):
        _, _, _, _, out = run(self.base)
        client, _, _, logs, _ = run(self.base, out=out)
        self.assertEqual(client.sent, [])
        self.assertEqual(sum(line.startswith("[SKIP][exists]") for line in logs), 3)
        self.assertEqual(sum(line.startswith("[SKIP][official]") for line in logs), 1)

    def test_source_files_are_not_changed(self):
        run(self.base)
        for rel, text in FILES.items():
            with open(os.path.join(self.quests, rel), encoding="utf-8") as f:
                self.assertEqual(f.read(), text)

    def test_selecting_quests_folder_directly(self):
        client, _, written, _, _ = run(self.quests)
        self.assertIn("lang/ru_ru.snbt", written)
        self.assertNotIn("lang/en_us.snbt", written)
        self.assertEqual(len(client.sent), 3)

    def test_selecting_lang_folder_directly(self):
        _, _, written, _, _ = run(os.path.join(self.quests, "lang"))
        self.assertEqual(written, ["ru_ru.snbt", "ru_ru/chapters/one.snbt"])

    def test_dry_run_writes_nothing_and_skips_foreign_locales(self):
        out = tempfile.mkdtemp()
        translator = Translator(
            FakeLLM(), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
            strict=True, log=lambda s: None, target_lang="ru_ru",
        )
        totals = []
        with contextlib.redirect_stdout(io.StringIO()):
            mirrorer.mirror_translate_dir(
                self.base, out, translator, log=lambda s: None, write=False,
                on_total=totals.append, target_lang="ru_ru",
            )
        self.assertEqual(totals, [4])
        self.assertEqual(os.listdir(out), [])


if __name__ == "__main__":
    unittest.main()
