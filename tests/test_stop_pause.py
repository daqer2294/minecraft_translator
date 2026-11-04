"""Пауза держит запросы, «Стоп» прерывает генерацию и не теряет сделанное, счётчики честные."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import zipfile
from unittest import mock

from src import config, mirrorer
from src.gui.api import Api, _Control, _StopRequested
from src.llm import factory
from src.llm.base import LLMCancelledError, LLMTimeoutError
from src.llm.local_llamacpp import LocalLlamaCppClient
from src.translators import Translator
from src.utils.cache import TranslationCache


class FakeLLM:
    name = "fake"

    def __init__(self, delay=0.003):
        self.delay = delay
        self.calls = 0
        self.lock = threading.Lock()

    def chat(self, messages, **kwargs):
        with self.lock:
            self.calls += 1
        time.sleep(self.delay)
        user = messages[-1]["content"]
        if "JSON array" in messages[0]["content"]:
            return json.dumps(["ПЕР " + x for x in json.loads(user)], ensure_ascii=False)
        return "ПЕР " + user


def add_jar(base, name, strings):
    mods = os.path.join(base, "mods")
    os.makedirs(mods, exist_ok=True)
    with zipfile.ZipFile(os.path.join(mods, f"{name}.jar"), "w") as z:
        z.writestr(f"assets/{name}/lang/en_us.json",
                   json.dumps({f"k{i}": f"Text {name} number {i}" for i in range(strings)}))


def add_loose(base, count, strings=30):
    for i in range(count):
        d = os.path.join(base, "kubejs", "assets", f"m{i}", "lang")
        os.makedirs(d)
        with open(os.path.join(d, "en_us.json"), "w", encoding="utf-8") as f:
            json.dump({f"k{j}": f"Long text {'x' * 230} {i} {j}" for j in range(strings)}, f)


def files_in(root):
    return [os.path.join(r, f) for r, _, fs in os.walk(root) for f in fs]


class RealPause(unittest.TestCase):
    def _requests_during_pause(self, base):
        ctl = _Control()
        client = FakeLLM()
        tr = Translator(
            client, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
            log=lambda s: None, target_lang="ru_ru", before_request=ctl.wait_if_paused,
        )
        out = tempfile.mkdtemp()

        def work():
            with contextlib.redirect_stdout(io.StringIO()):
                mirrorer.mirror_translate_dir(
                    base, out, tr, log=lambda s: None,
                    on_tick=lambda *a: ctl.checkpoint(), target_lang="ru_ru",
                )

        thread = threading.Thread(target=work)
        thread.start()
        time.sleep(0.05)
        ctl.pause()
        time.sleep(0.1)  # запросы, начатые до паузы, успевают закончиться
        before = client.calls
        time.sleep(0.4)
        during = client.calls - before
        ctl.resume()
        thread.join()
        return during, client.calls, out

    def test_pause_holds_requests_in_jar_loop_and_resume_finishes(self):
        base = tempfile.mkdtemp()
        add_jar(base, "big", 300)
        during, total, out = self._requests_during_pause(base)
        self.assertEqual(during, 0)
        self.assertEqual(total, 300)
        self.assertTrue(os.path.exists(os.path.join(out, "MinecraftTranslator_ru_ru.zip")))

    def test_pause_holds_requests_in_file_workers(self):
        base = tempfile.mkdtemp()
        add_loose(base, 20)
        during, _, _ = self._requests_during_pause(base)
        self.assertEqual(during, 0)

    def test_stop_releases_paused_workers(self):
        ctl = _Control()
        ctl.pause()
        released = threading.Event()

        def wait():
            ctl.wait_if_paused()
            released.set()

        threading.Thread(target=wait, daemon=True).start()
        self.assertFalse(released.wait(0.1))
        ctl.stop()
        self.assertTrue(released.wait(1.0))


class StopInJarLoop(unittest.TestCase):
    def setUp(self):
        self.base, self.out = tempfile.mkdtemp(), tempfile.mkdtemp()
        for name in ("a", "b", "c"):
            add_jar(self.base, name, 200)
        self.cancel = threading.Event()
        self.ctl = _Control()
        self.cache_path = os.path.join(tempfile.mkdtemp(), "c.json")
        self.logs, self.ticks = [], []
        self.client = FakeLLM()
        translator = Translator(
            self.client, TranslationCache(self.cache_path), strict=True, log=self.logs.append,
            target_lang="ru_ru", cancel_event=self.cancel,
        )

        def on_tick(*args):
            self.ticks.append(args)
            self.ctl.checkpoint()

        threading.Timer(0.25, lambda: (self.ctl.stop(), self.cancel.set())).start()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.suppress(_StopRequested):
            mirrorer.mirror_translate_dir(
                self.base, self.out, translator, log=self.logs.append, on_tick=on_tick,
                cancel_event=self.cancel, target_lang="ru_ru",
            )

    def test_no_partial_output(self):
        self.assertEqual(files_in(self.out), [])

    def test_no_empty_error_lines(self):
        self.assertEqual([x for x in self.logs if x.startswith("[ERR]")], [])

    def test_each_jar_is_counted_once(self):
        self.assertLessEqual(sum(t[0] for t in self.ticks), 3)
        self.assertEqual(sum(t[2] for t in self.ticks), 0)

    def test_finished_strings_are_on_disk_right_after_stop(self):
        with open(self.cache_path, encoding="utf-8") as f:
            cached = json.load(f)["ru_ru"]
        self.assertEqual(len(cached), self.client.calls)

    def test_next_run_reuses_cache_and_finishes_the_pack(self):
        client = FakeLLM(delay=0)
        translator = Translator(
            client, TranslationCache(self.cache_path), strict=True, log=lambda s: None, target_lang="ru_ru",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            mirrorer.mirror_translate_dir(self.base, self.out, translator, log=lambda s: None, target_lang="ru_ru")
        self.assertEqual(client.calls + self.client.calls, 600)
        with zipfile.ZipFile(os.path.join(self.out, "MinecraftTranslator_ru_ru.zip")) as z:
            for name in ("a", "b", "c"):
                self.assertEqual(len(json.loads(z.read(f"assets/{name}/lang/ru_ru.json"))), 200)


class Counters(unittest.TestCase):
    def _run(self, base, out):
        ticks = []
        translator = Translator(
            FakeLLM(delay=0), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
            log=lambda s: None, target_lang="ru_ru",
        )
        logs = []
        with contextlib.redirect_stdout(io.StringIO()):
            mirrorer.mirror_translate_dir(
                base, out, translator, log=logs.append, on_tick=lambda *a: ticks.append(a), target_lang="ru_ru",
            )
        return ticks, logs

    def test_finished_file_is_only_skip(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        add_loose(base, 1, strings=1)
        os.makedirs(os.path.join(out, "kubejs", "assets", "m0", "lang"))
        with open(os.path.join(out, "kubejs", "assets", "m0", "lang", "ru_ru.json"), "w") as f:
            f.write("{}")
        self.assertEqual(self._run(base, out)[0], [(1, 0, 0, 1)])

    def test_translated_file_is_only_ok(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        add_loose(base, 1, strings=1)
        self.assertEqual(self._run(base, out)[0], [(1, 1, 0, 0)])

    def test_fully_official_jar_is_skip(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        os.makedirs(os.path.join(base, "mods"))
        with zipfile.ZipFile(os.path.join(base, "mods", "x.jar"), "w") as z:
            z.writestr("assets/x/lang/en_us.json", json.dumps({"a": "Iron"}))
            z.writestr("assets/x/lang/ru_ru.json", json.dumps({"a": "Железо"}, ensure_ascii=False))
        self.assertEqual(self._run(base, out)[0], [(1, 0, 0, 1)])

    def test_broken_jar_error_has_text_and_counts_as_error(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        add_jar(base, "x", 1)
        with mock.patch.object(mirrorer.jar_lang, "process_jar_lang", side_effect=KeyError()):
            ticks, logs = self._run(base, out)
        self.assertEqual(ticks, [(1, 0, 1, 0)])
        self.assertIn("[ERR][jar] x.jar: KeyError", logs)


class InProcessAbort(unittest.TestCase):
    """Настоящая остановка генерации проверена на Qwen; здесь её логика без модели."""

    def _client(self, on_token):
        module = types.ModuleType("llama_cpp")

        class Llama:
            def __init__(self, **kwargs):
                pass

            def create_chat_completion(self, stream=False, **kwargs):
                for _ in range(200):
                    on_token()
                    time.sleep(0.005)
                    yield {"choices": [{"delta": {"content": "о"}, "finish_reason": None}]}
                yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

        module.Llama = Llama
        for patcher in (mock.patch.dict(sys.modules, {"llama_cpp": module}),
                        mock.patch("os.path.exists", return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return LocalLlamaCppClient(model_path="m.gguf")

    def test_cancel_stops_generation(self):
        cancel = threading.Event()
        count = {"n": 0}

        def token():
            count["n"] += 1
            if count["n"] == 5:
                cancel.set()

        with self.assertRaises(LLMCancelledError):
            self._client(token).chat([{"role": "user", "content": "x"}], cancel_event=cancel)
        self.assertEqual(count["n"], 5)

    def test_timeout_stops_generation(self):
        count = {"n": 0}

        def token():
            count["n"] += 1

        with self.assertRaises(LLMTimeoutError):
            self._client(token).chat([{"role": "user", "content": "x"}], timeout=0.05)
        self.assertLess(count["n"], 200)

    def test_no_limit_runs_to_the_end(self):
        client = self._client(lambda: None)
        self.assertEqual(client.chat([{"role": "user", "content": "x"}]), "о" * 200)


class ApiWiring(unittest.TestCase):
    def test_api_pause_and_stop_reach_a_running_translation(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        add_jar(base, "a", 400)
        client = FakeLLM()
        cache_path = os.path.join(tempfile.mkdtemp(), "c.json")
        with mock.patch.object(factory, "build_clients", return_value=(client, None)), \
                mock.patch.object(config, "DEFAULT_CACHE_PATH", cache_path):
            api = Api()
            api._set(input=base, output=out, dry=False, mode="ollama")
            with mock.patch.object(config.PROVIDER, "ollama_model", "m"):
                api.start()
                time.sleep(0.1)
                api.pause()
                time.sleep(0.1)
                before = client.calls
                time.sleep(0.3)
                self.assertEqual(client.calls, before)
                api.resume()
                time.sleep(0.1)
                self.assertGreater(client.calls, before)
                api.stop()
                api._worker.join(5)
        self.assertFalse(api._worker.is_alive())
        self.assertEqual(api._state["phase"], "stopped")
        self.assertFalse(os.path.exists(os.path.join(out, "MinecraftTranslator_ru_ru.zip")))


if __name__ == "__main__":
    unittest.main()
