"""Разметка не отправляется в модель и возвращается на место без потерь."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

from src.translators import Translator
from src.utils.cache import TranslationCache
from src.utils.markup import mask, unmask


def make_translator(client):
    tr = Translator(
        client, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
        strict=True, log=lambda s: None, target_lang="ru_ru",
    )
    tr.max_attempts = 1
    return tr


class Scripted:
    name = "scripted"

    def __init__(self, reply=None):
        self.reply = reply or (lambda t: "ПЕР " + t)
        self.sent = []

    def chat(self, messages, **kwargs):
        user = messages[-1]["content"]
        if "JSON array" in messages[0]["content"]:
            items = json.loads(user)
            self.sent += items
            return json.dumps([self.reply(x) for x in items], ensure_ascii=False)
        self.sent.append(user)
        return self.reply(user)


class MaskRoundTrip(unittest.TestCase):
    CASES = [
        "&9Crafting Components Blueprints ", "Hello %s and {0}", "§aGreen §rtext", "%1$s: %2$s text",
        "Use <red>this</red> now", "Line one\nLine two", "A\\nB", "$(item)Iron$() Ingot",
        "{@pagebreak}Next page", "Press minecraft:stone now", "100% sure", "Sale 10%off",
        "Ratio 1:2 is ok", "Note:example text", "Visit https://x.io/a now", "  spaced  ",
        "&3&lBosses&r", "{image:atm:textures/x.png width:100 height:50}Look", "Plain text", "%d%%",
    ]

    def test_masked_text_restores_exactly(self):
        for text in self.CASES:
            with self.subTest(text):
                m = mask(text)
                self.assertEqual(unmask(m.core, m) if m.core else m.prefix + m.suffix, text)

    def test_edges_are_removed_and_inner_markup_becomes_tokens(self):
        m = mask("&3&lOne %s two&r")
        self.assertEqual((m.prefix, m.core, m.suffix, m.tokens), ("&3&l", "One [[0]] two", "&r", ["%s"]))

    def test_words_that_only_look_like_markup_stay_text(self):
        for text in ("100% sure", "Sale 10%off", "Ratio 1:2 is ok", "Note:example text"):
            self.assertEqual(mask(text).tokens, [], text)

    def test_lost_or_duplicated_token_is_rejected(self):
        m = mask("One %s two %d three")
        self.assertIsNone(unmask("Раз [[0]] два", m))
        self.assertIsNone(unmask("Раз [[0]] [[0]] [[1]]", m))
        self.assertIsNone(unmask("", m))

    def test_tokens_may_change_order(self):
        m = mask("One %s two %d three")
        self.assertEqual(unmask("[[1]] раз [[0]]", m), "%d раз %s")

    def test_model_spacing_around_token_is_tolerated(self):
        m = mask("One %s two")
        self.assertEqual(unmask("Раз [[ 0 ]] два", m), "Раз %s два")

    def test_space_added_after_color_code_is_removed(self):
        m = mask("One &bTwo")
        self.assertEqual(unmask("Раз [[0]] Два", m), "Раз &bДва")

    def test_space_present_in_source_is_kept(self):
        m = mask("One &b Two")
        self.assertEqual(unmask("Раз [[0]] Два", m), "Раз &b Два")


class PromptNote(unittest.TestCase):
    def _prompts(self, text):
        prompts = []

        class Client:
            name = "c"

            def chat(self, messages, **kwargs):
                prompts.append(messages[0]["content"])
                return "ПЕР " + messages[-1]["content"]

        make_translator(Client()).translate(text)
        return prompts

    def test_note_only_when_tokens_are_in_the_text(self):
        self.assertNotIn("[[0]]", self._prompts("Leaves")[0])
        self.assertNotIn("[[0]]", self._prompts("&aLeaves&r")[0])
        self.assertNotIn("[[0]]", self._prompts("Hello %s")[0])
        self.assertIn("[[0]]", self._prompts("One %s two")[0])

    def test_invented_token_is_rejected(self):
        client = Scripted(lambda t: "[[0]] Листья [[1]]")
        tr = make_translator(client)
        self.assertEqual(tr.translate("Leaves"), "Leaves")
        self.assertIsNone(tr.cache.get("Leaves", "ru_ru"))


class TranslatorMasking(unittest.TestCase):
    def test_model_sees_no_markup_and_result_has_it_back(self):
        client = Scripted()
        out = make_translator(client).translate("&9Iron %s Ingot&r")
        self.assertEqual(client.sent, ["Iron [[0]] Ingot"])
        self.assertEqual(out, "&9ПЕР Iron %s Ingot&r")

    def test_lost_token_is_retried_without_mask(self):
        def reply(text):
            return "Потеряно" if "[[" in text else "Железо %s слиток"

        client = Scripted(reply)
        out = make_translator(client).translate("Iron %s ingot")
        self.assertEqual(client.sent, ["Iron [[0]] ingot", "Iron %s ingot"])
        self.assertEqual(out, "Железо %s слиток")

    def test_lost_token_twice_falls_back_to_source_uncached(self):
        tr = make_translator(Scripted(lambda t: "Потеряно"))
        self.assertEqual(tr.translate("Iron %s ingot"), "Iron %s ingot")
        self.assertIsNone(tr.cache.get("Iron %s ingot", "ru_ru"))

    def test_translation_is_cached_with_markup(self):
        tr = make_translator(Scripted())
        tr.translate("&aIron")
        self.assertEqual(tr.cache.get("&aIron", "ru_ru"), "&aПЕР Iron")

    def test_batch_masks_and_restores(self):
        client = Scripted()
        out = make_translator(client).translate_many(["&aIron", "Gold %s bar"])
        self.assertEqual(client.sent, ["Iron", "Gold [[0]] bar"])
        self.assertEqual(out, ["&aПЕР Iron", "ПЕР Gold %s bar"])

    def test_batch_item_with_lost_token_goes_single(self):
        calls = []

        def reply(text):
            calls.append(text)
            return "Потеряно" if text == "Gold [[0]] bar" and calls.count(text) == 1 else "ПЕР " + text

        tr = make_translator(Scripted(reply))
        with contextlib.redirect_stdout(io.StringIO()):
            out = tr.translate_many(["Iron", "Gold %s bar"])
        self.assertEqual(out, ["ПЕР Iron", "ПЕР Gold %s bar"])

    def test_only_markup_is_not_sent(self):
        client = Scripted()
        tr = make_translator(client)
        self.assertEqual(tr.translate("&a%s &r"), "&a%s &r")
        self.assertEqual(tr.translate("{image:a:b width:1}"), "{image:a:b width:1}")
        self.assertEqual(client.sent, [])


if __name__ == "__main__":
    unittest.main()
