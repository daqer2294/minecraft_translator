"""Сбой модели не должен оставлять английский в кэше навсегда."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import threading
import unittest

from src.llm.base import LLMCancelledError, LLMTimeoutError
from src.translators import Translator
from src.utils.cache import TranslationCache


class Scripted:
    """Отвечает заданной функцией от текста или бросает исключение."""

    name = "scripted"

    def __init__(self, reply=None, exc=None, on_call=None):
        self.reply, self.exc, self.on_call = reply, exc, on_call
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        if self.on_call:
            self.on_call()
        if self.exc:
            raise self.exc
        user = messages[-1]["content"]
        if "JSON array" in messages[0]["content"]:
            return json.dumps([self.reply(x) for x in json.loads(user)], ensure_ascii=False)
        return self.reply(user)


def translated(text):
    return "ПЕР " + text


class CacheFallbacks(unittest.TestCase):
    def setUp(self):
        self.cache = TranslationCache(os.path.join(tempfile.mkdtemp(), "cache.json"))

    def _translator(self, client, cancel_event=None):
        tr = Translator(
            client, self.cache, strict=True, log=lambda s: None,
            target_lang="ru_ru", cancel_event=cancel_event,
        )
        tr.max_attempts = 1
        return tr

    def _assert_recovers(self, text):
        self.assertIsNone(self.cache.get(text, "ru_ru"))
        healthy = Scripted(reply=translated)
        self.assertEqual(self._translator(healthy).translate(text), "ПЕР " + text)
        self.assertEqual(healthy.calls, 1)

    def _quiet(self):
        return contextlib.redirect_stdout(io.StringIO())

    def test_timeout_single(self):
        with self._quiet():
            out = self._translator(Scripted(exc=LLMTimeoutError("t"))).translate("Diamond Sword")
        self.assertEqual(out, "Diamond Sword")
        self._assert_recovers("Diamond Sword")

    def test_timeout_batch(self):
        with self._quiet():
            out = self._translator(Scripted(exc=LLMTimeoutError("t"))).translate_many(["Iron Ingot", "Gold Ingot"])
        self.assertEqual(out, ["Iron Ingot", "Gold Ingot"])
        self._assert_recovers("Iron Ingot")
        self._assert_recovers("Gold Ingot")

    def test_server_error_single_and_batch(self):
        with self._quiet():
            self._translator(Scripted(exc=RuntimeError("HTTP 500"))).translate("Silver Sword")
            self._translator(Scripted(exc=RuntimeError("HTTP 500"))).translate_many(["Tin Ingot", "Lead Ingot"])
        for text in ("Silver Sword", "Tin Ingot", "Lead Ingot"):
            self._assert_recovers(text)

    def test_cancel_in_flight(self):
        cancel = threading.Event()
        client = Scripted(exc=LLMCancelledError("c"), on_call=cancel.set)
        with self._quiet():
            out = self._translator(client, cancel).translate("Copper Sword")
        self.assertEqual(out, "Copper Sword")
        self._assert_recovers("Copper Sword")

    def test_empty_answer_returns_source_and_is_not_cached(self):
        out = self._translator(Scripted(reply=lambda t: "")).translate("Empty Case")
        self.assertEqual(out, "Empty Case")
        self._assert_recovers("Empty Case")

    def test_empty_item_in_batch_returns_source_and_is_not_cached(self):
        out = self._translator(Scripted(reply=lambda t: "" if t == "Bad" else translated(t))).translate_many(["Bad", "Good"])
        self.assertEqual(out, ["Bad", "ПЕР Good"])
        self.assertIsNone(self.cache.get("Bad", "ru_ru"))
        self.assertEqual(self.cache.get("Good", "ru_ru"), "ПЕР Good")

    def test_lost_placeholder_is_not_cached(self):
        out = self._translator(Scripted(reply=lambda t: "Слиток")).translate("Ingot %s")
        self.assertEqual(out, "Ingot %s")
        self._assert_recovers("Ingot %s")

    def test_answer_equal_to_source_is_not_cached(self):
        self._translator(Scripted(reply=lambda t: t)).translate("Sodium")
        self._assert_recovers("Sodium")

    def test_real_translation_is_cached_and_reused(self):
        client = Scripted(reply=translated)
        tr = self._translator(client)
        tr.translate("Emerald")
        tr.translate("Emerald")
        self.assertEqual(client.calls, 1)
        self.assertEqual(self.cache.get("Emerald", "ru_ru"), "ПЕР Emerald")


if __name__ == "__main__":
    unittest.main()
