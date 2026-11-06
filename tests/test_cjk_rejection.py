"""Иероглифы в ответе модели при нецелевом для них языке: это брак, а не перевод."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

from src.translators import Translator, _has_foreign_cjk
from src.utils.cache import TranslationCache


class Scripted:
    name = "scripted"

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        user = messages[-1]["content"]
        if "JSON array" in messages[0]["content"]:
            return json.dumps([self.reply(x) for x in json.loads(user)], ensure_ascii=False)
        return self.reply(user)


def make_translator(client, target_lang="ru_ru"):
    tr = Translator(
        client, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
        strict=True, log=lambda s: None, target_lang=target_lang,
    )
    tr.max_attempts = 1
    return tr


class Detection(unittest.TestCase):
    def test_all_cjk_ranges_are_foreign_for_russian(self):
        for text in ("Необходимо至少", "カタカナ", "ひらがな", "한국어", "㐀", "豈"):
            self.assertTrue(_has_foreign_cjk(text, "ru_ru"), text)

    def test_other_scripts_are_fine(self):
        for text in ("Железный слиток", "Iron", "§7Текст %s", "Ελληνικά", "العربية", "█ ★ → …", "日"[:0]):
            self.assertFalse(_has_foreign_cjk(text, "ru_ru"), text)

    def test_cjk_target_languages_are_allowed(self):
        for lang in ("zh_cn", "zh_tw", "zh_hk", "lzh", "ja_jp", "ko_kr", "ZH_CN"):
            self.assertFalse(_has_foreign_cjk("至少 カナ 한국어", lang), lang)

    def test_other_targets_are_checked(self):
        for lang in ("de_de", "en_us", "uk_ua", "kk_kz"):
            self.assertTrue(_has_foreign_cjk("至少", lang), lang)


class Rejection(unittest.TestCase):
    def test_single_answer_returns_source_and_is_not_cached(self):
        tr = make_translator(Scripted(lambda t: "Необходимо至少 Netherite"))
        self.assertEqual(tr.translate("Needs at least Netherite"), "Needs at least Netherite")
        self.assertEqual(len(tr.cache), 0)

    def test_markup_is_kept_on_rollback(self):
        tr = make_translator(Scripted(lambda t: "至少"))
        self.assertEqual(tr.translate("§7Needs %s more"), "§7Needs %s more")

    def test_batch_item_is_rolled_back_and_others_kept(self):
        tr = make_translator(Scripted(lambda t: "Брак至少" if t == "Bad" else "ПЕР " + t))
        with contextlib.redirect_stdout(io.StringIO()):
            out = tr.translate_many(["Bad", "Good"])
        self.assertEqual(out, ["Bad", "ПЕР Good"])
        self.assertIsNone(tr.cache.get("Bad", "ru_ru"))
        self.assertEqual(tr.cache.get("Good", "ru_ru"), "ПЕР Good")

    def test_rejected_answer_is_asked_again_on_next_run(self):
        client = Scripted(lambda t: "至少")
        tr = make_translator(client)
        tr.translate("Needs more")
        # В том же прогоне не спрашиваем: при температуре 0 ответ был бы тем же.
        tr.translate("Needs more")
        self.assertEqual(client.calls, 1)
        nxt = Translator(client, tr.cache, strict=True, log=lambda s: None, target_lang="ru_ru")
        nxt.max_attempts = 1
        nxt.translate("Needs more")
        self.assertEqual(client.calls, 2)

    def test_chinese_target_keeps_the_answer(self):
        tr = make_translator(Scripted(lambda t: "至少需要"), target_lang="zh_cn")
        self.assertEqual(tr.translate("Needs more"), "至少需要")
        self.assertEqual(tr.cache.get("Needs more", "zh_cn"), "至少需要")

    def test_validation_off_still_rejects(self):
        client = Scripted(lambda t: "至少")
        tr = make_translator(client)
        tr.strict = False
        self.assertEqual(tr.translate("Needs more"), "Needs more")


if __name__ == "__main__":
    unittest.main()
