"""Регрессия по прошлым фиксам. Запуск из корня репозитория: python -m unittest discover -s tests -t .

Настоящая модель не нужна: LLM заменён подставным клиентом или локальным сокетом.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import zipfile

from src import config, mirrorer
from src.gui.api import _Control, _StopRequested
from src.llm.base import LLMCancelledError, LLMTimeoutError
from src.llm.openai_compatible import OpenAICompatibleClient
from src.translators import Translator
from src.utils.cache import TranslationCache


class FakeLLM:
    """Отвечает «ПЕР <текст>» и считает запросы."""

    name = "fake"

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = 0
        self.prompts = []
        self._lock = threading.Lock()

    def chat(self, messages, **kwargs):
        with self._lock:
            self.calls += 1
            self.prompts.append(messages[0]["content"])
        if self.delay:
            time.sleep(self.delay)
        user = messages[-1]["content"]
        if "JSON array" in messages[0]["content"]:
            return json.dumps(["ПЕР " + x for x in json.loads(user)], ensure_ascii=False)
        return "ПЕР " + user


def make_translator(client, cancel_event=None, target_lang="ru_ru"):
    cache = TranslationCache(os.path.join(tempfile.mkdtemp(), "cache.json"))
    return Translator(
        client, cache, strict=True, log=lambda s: None,
        target_lang=target_lang, cancel_event=cancel_event,
    )


def make_jar(mods_dir, name, strings):
    os.makedirs(mods_dir, exist_ok=True)
    path = os.path.join(mods_dir, name)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            f"assets/{name[:-4]}/lang/en_us.json",
            json.dumps({f"k{i}": f"Hello number {i}" for i in range(strings)}),
        )
    return path


def make_loose_lang(base, count, strings=3):
    # Папка kubejs остаётся на своём пути: отдельные файлы, а не содержимое ресурспака
    for i in range(count):
        d = os.path.join(base, "kubejs", "assets", f"m{i}", "lang")
        os.makedirs(d)
        with open(os.path.join(d, "en_us.json"), "w", encoding="utf-8") as f:
            json.dump({f"k{j}": f"Text {i} number {j}" for j in range(strings)}, f)


def output_files(root):
    return [os.path.join(r, f) for r, _, fs in os.walk(root) for f in fs]


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


class ModsJarDetection(unittest.TestCase):
    """c96aae7: jar лежат прямо в <instance>/mods/, каталог без хвостового «/»."""

    def test_jar_directly_in_mods_is_found_and_translated(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        make_jar(os.path.join(base, "mods"), "a.jar", 2)
        totals = []
        with quiet():
            mirrorer.mirror_translate_dir(
                base, out, make_translator(FakeLLM()), log=lambda s: None,
                on_total=totals.append, target_lang="ru_ru",
            )
        self.assertEqual(totals, [1])
        with zipfile.ZipFile(os.path.join(out, "MinecraftTranslator_ru_ru.zip")) as z:
            self.assertIn("assets/a/lang/ru_ru.json", z.namelist())

    def test_jar_outside_mods_is_ignored(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        make_jar(os.path.join(base, "libraries"), "a.jar", 2)
        totals = []
        with quiet():
            mirrorer.mirror_translate_dir(
                base, out, make_translator(FakeLLM()), log=lambda s: None,
                on_total=totals.append, target_lang="ru_ru",
            )
        self.assertEqual(totals, [0])


class QueueCancel(unittest.TestCase):
    """8154f71: «Стоп» отменяет очередь и не оставляет непереведённых файлов."""

    def test_stop_cancels_queue_and_writes_no_untranslated_loose_file(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        make_loose_lang(base, 60)
        cancel = threading.Event()
        client = FakeLLM(delay=0.005)
        translator = make_translator(client, cancel_event=cancel)
        ctl = _Control()
        threading.Timer(0.08, lambda: (ctl.stop(), cancel.set())).start()
        with quiet(), contextlib.suppress(_StopRequested):
            mirrorer.mirror_translate_dir(
                base, out, translator, log=lambda s: None,
                on_tick=lambda *a: ctl.checkpoint(),
                cancel_event=cancel, target_lang="ru_ru",
            )
        calls_at_stop = client.calls
        time.sleep(0.1)
        self.assertEqual(client.calls, calls_at_stop, "после Стоп запросы продолжаются")
        self.assertLess(calls_at_stop, 60 * 3, "очередь не отменилась")
        for path in output_files(out):
            with open(path, encoding="utf-8") as f:
                for value in json.load(f).values():
                    self.assertTrue(value.startswith("ПЕР "), f"{path}: {value}")


class LanguageSnapshot(unittest.TestCase):
    """66d3ecf: смена языка посреди прогона не влияет на идущий прогон."""

    def test_language_change_mid_run_is_ignored(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        make_loose_lang(base, 8)
        original = config.TARGET_LANG
        client = FakeLLM(delay=0.003)
        translator = make_translator(client, target_lang="ru_ru")
        timer = threading.Timer(0.01, lambda: setattr(config, "TARGET_LANG", "de_de"))
        timer.start()
        try:
            with quiet():
                mirrorer.mirror_translate_dir(
                    base, out, translator, log=lambda s: None, target_lang="ru_ru",
                )
        finally:
            timer.join()
            config.TARGET_LANG = original
        names = {os.path.basename(p) for p in output_files(out)}
        self.assertEqual(names, {"ru_ru.json"})
        self.assertEqual(set(translator.cache.data), {"ru_ru"})


class SilentServer:
    """Принимает соединение и молчит, как зависшая модель."""

    def __enter__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.conns = []
        threading.Thread(target=self._accept, daemon=True).start()
        return f"http://127.0.0.1:{self.sock.getsockname()[1]}"

    def _accept(self):
        with contextlib.suppress(OSError):
            while True:
                self.conns.append(self.sock.accept()[0])

    def __exit__(self, *exc):
        self.sock.close()
        for c in self.conns:
            c.close()


class LLMTimeoutAndCancel(unittest.TestCase):
    """69edc48: запрос к зависшей модели завершается по таймауту и по отмене."""

    def _client(self, url):
        return OpenAICompatibleClient(base_url=url, api_key="", model="m")

    def test_timeout(self):
        with SilentServer() as url:
            started = time.time()
            with self.assertRaises(LLMTimeoutError):
                self._client(url).chat([{"role": "user", "content": "x"}], timeout=0.3)
            self.assertLess(time.time() - started, 3.0)

    def test_cancel_in_flight(self):
        with SilentServer() as url:
            cancel = threading.Event()
            threading.Timer(0.2, cancel.set).start()
            started = time.time()
            with self.assertRaises(LLMCancelledError):
                self._client(url).chat(
                    [{"role": "user", "content": "x"}], timeout=30, cancel_event=cancel,
                )
            self.assertLess(time.time() - started, 3.0)


class JarAtStop(unittest.TestCase):
    """Стоп посреди jar-цикла."""

    def _run(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        mods = os.path.join(base, "mods")
        for name in ("a.jar", "b.jar", "c.jar"):
            make_jar(mods, name, 200)
        cancel = threading.Event()
        translator = make_translator(FakeLLM(delay=0.002), cancel_event=cancel)
        ctl = _Control()
        logs = []
        threading.Timer(0.2, lambda: (ctl.stop(), cancel.set())).start()
        with quiet(), contextlib.suppress(_StopRequested):
            mirrorer.mirror_translate_dir(
                base, out, translator, log=logs.append,
                on_tick=lambda *a: ctl.checkpoint(),
                cancel_event=cancel, target_lang="ru_ru",
            )
        return out, logs

    def test_stop_prevents_starting_next_jars(self):
        _, logs = self._run()
        self.assertLess(sum(line.startswith("[OK][jar]") for line in logs), 3)

    def test_stop_writes_no_partial_output(self):
        out, _ = self._run()
        self.assertEqual(output_files(out), [])


if __name__ == "__main__":
    unittest.main()
