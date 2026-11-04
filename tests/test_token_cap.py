"""Лимит ответа: зациклившаяся модель не тратит минуты и не попадает в кэш."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from src.llm import factory
from src.llm.base import LLMTruncatedError
from src.llm.local_llamacpp import LocalLlamaCppClient
from src.llm.openai_compatible import OpenAICompatibleClient
from src.translators import Translator, _batch_token_cap, _single_token_cap
from src.utils.cache import TranslationCache


class Recorder:
    name = "recorder"

    def __init__(self, truncate_batches=False, truncate_singles=False):
        self.kwargs = []
        self.truncate_batches = truncate_batches
        self.truncate_singles = truncate_singles

    def chat(self, messages, **kwargs):
        self.kwargs.append(kwargs)
        user = messages[-1]["content"]
        if "JSON array" in messages[0]["content"]:
            if self.truncate_batches:
                raise LLMTruncatedError("оборвано")
            return json.dumps(["ПЕР " + x for x in json.loads(user)], ensure_ascii=False)
        if self.truncate_singles:
            raise LLMTruncatedError("оборвано")
        return "ПЕР " + user


def make_translator(client):
    return Translator(
        client, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
        strict=True, log=lambda s: None, target_lang="ru_ru",
    )


class Caps(unittest.TestCase):
    def test_cap_values(self):
        self.assertEqual(_single_token_cap("short"), 128)
        self.assertEqual(_single_token_cap("x" * 1000), 1500)
        self.assertEqual(_batch_token_cap(["a", "b"]), 32 + 32 + 64)
        self.assertEqual(_batch_token_cap(["x" * 100]), 150 + 64)

    def test_single_request_sends_cap(self):
        client = Recorder()
        make_translator(client).translate("Iron Ingot")
        self.assertEqual(client.kwargs[0]["max_tokens"], 128)

    def test_batch_request_sends_cap(self):
        client = Recorder()
        make_translator(client).translate_many(["Iron Ingot", "Gold Ingot"])
        self.assertEqual(client.kwargs[0]["max_tokens"], _batch_token_cap(["Iron Ingot", "Gold Ingot"]))


class Truncation(unittest.TestCase):
    def quiet(self):
        return contextlib.redirect_stdout(io.StringIO())

    def test_truncated_single_returns_source_and_is_not_cached(self):
        tr = make_translator(Recorder(truncate_singles=True))
        with self.quiet():
            self.assertEqual(tr.translate("Iron Ingot"), "Iron Ingot")
        self.assertIsNone(tr.cache.get("Iron Ingot", "ru_ru"))

    def test_truncated_batch_falls_back_to_single_requests(self):
        client = Recorder(truncate_batches=True)
        tr = make_translator(client)
        with self.quiet():
            self.assertEqual(tr.translate_many(["Iron Ingot", "Gold Ingot"]), ["ПЕР Iron Ingot", "ПЕР Gold Ingot"])
        self.assertEqual(len(client.kwargs), 3)

    def test_truncation_is_not_retried(self):
        client = Recorder(truncate_singles=True)
        with self.quiet():
            make_translator(client).translate("Iron Ingot")
        self.assertEqual(len(client.kwargs), 1)


class InProcessClient(unittest.TestCase):
    def _client(self, finish):
        module = types.ModuleType("llama_cpp")

        class Llama:
            seen = {}

            def __init__(self, **kwargs):
                pass

            def create_chat_completion(self, **kwargs):
                Llama.seen.update(kwargs)
                return {"choices": [{"message": {"content": "ответ"}, "finish_reason": finish}]}

        module.Llama = Llama
        for patcher in (mock.patch.dict(sys.modules, {"llama_cpp": module}),
                        mock.patch("os.path.exists", return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return LocalLlamaCppClient(model_path="m.gguf"), Llama

    def test_length_finish_raises(self):
        client, _ = self._client("length")
        with self.assertRaises(LLMTruncatedError):
            client.chat([{"role": "user", "content": "x"}], max_tokens=4)

    def test_cap_reaches_llama_and_stop_finish_returns_text(self):
        client, llama = self._client("stop")
        self.assertEqual(client.chat([{"role": "user", "content": "x"}], max_tokens=4), "ответ")
        self.assertEqual(llama.seen["max_tokens"], 4)


class HttpClient(unittest.TestCase):
    def _serve(self, finish):
        bodies = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                payload = json.dumps({"choices": [{"message": {"content": "ответ"}, "finish_reason": finish}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}", bodies

    def test_length_finish_raises_and_stop_returns_text(self):
        url, bodies = self._serve("length")
        with self.assertRaises(LLMTruncatedError):
            OpenAICompatibleClient(base_url=url, model="m").chat([{"role": "user", "content": "x"}], max_tokens=7)
        self.assertEqual(bodies[0]["max_tokens"], 7)
        url, _ = self._serve("stop")
        self.assertEqual(OpenAICompatibleClient(base_url=url, model="m").chat([{"role": "user", "content": "x"}]), "ответ")

    def test_external_provider_gets_no_cap(self):
        # Часть моделей внешнего API отвергает max_tokens с HTTP 400, и зацикливания у них не было.
        url, bodies = self._serve("stop")
        cfg = types.SimpleNamespace(external_base_url=url, external_api_key="", external_model="m")
        factory._make_external(cfg).chat([{"role": "user", "content": "x"}], max_tokens=7)
        self.assertNotIn("max_tokens", bodies[0])


if __name__ == "__main__":
    unittest.main()
