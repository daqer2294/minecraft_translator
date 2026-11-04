"""Второй проход: брак основной модели переводит сильная, только эти строки и через тот же валидатор."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import threading
import time
import types
import unittest
import zipfile
from unittest import mock

from src import config, mirrorer
from src.gui.api import Api, _Control, _StopRequested
from src.llm import factory, ollama_probe
from src.llm.base import LLMClientError, LLMTimeoutError, LLMTruncatedError
from src.translators import Translator
from src.utils.cache import TranslationCache

ZIP_NAME = "MinecraftTranslator_ru_ru.zip"
BAD = "Мinecraft"


class Model:
    """reply(текст) -> ответ или исключение. Записывает, что у него спрашивали."""

    def __init__(self, name, reply, delay=0.0):
        self.name = name
        self.reply = reply
        self.delay = delay
        self.sent = []

    def chat(self, messages, **kwargs):
        user = messages[-1]["content"]
        if "JSON array" in messages[0]["content"]:
            items = json.loads(user)
            self.sent += items
            return json.dumps([self.reply(x) for x in items], ensure_ascii=False)
        self.sent.append(user)
        if self.delay:
            time.sleep(self.delay)
        answer = self.reply(user)
        if isinstance(answer, Exception):
            raise answer
        return answer


def good(text):
    return "ПЕР " + text


def bad_for(*bad_strings, answer=BAD):
    return lambda text: answer if text in bad_strings else good(text)


def make_jar(base, name, en, ru=None):
    mods = os.path.join(base, "mods")
    os.makedirs(mods, exist_ok=True)
    with zipfile.ZipFile(os.path.join(mods, f"{name}.jar"), "w") as z:
        z.writestr(f"assets/{name}/lang/en_us.json", json.dumps(en))
        if ru is not None:
            z.writestr(f"assets/{name}/lang/ru_ru.json", json.dumps(ru, ensure_ascii=False))


def run(base, primary, strong=None, reason="нет сильной модели", cache_path=None, on_tick=None, cancel=None, write=True):
    out = tempfile.mkdtemp()
    cache_path = cache_path or os.path.join(tempfile.mkdtemp(), "c.json")
    logs = []
    translator = Translator(
        primary, TranslationCache(cache_path), strict=True, log=lambda s: None,
        target_lang="ru_ru", cancel_event=cancel,
    )
    translator.max_attempts = 1
    with contextlib.redirect_stdout(io.StringIO()):
        mirrorer.mirror_translate_dir(
            base, out, translator, log=logs.append, write=write, on_tick=on_tick, cancel_event=cancel,
            target_lang="ru_ru", strong_client=lambda: (strong, reason),
        )
    return out, translator, logs


def pack_lang(out, namespace):
    with zipfile.ZipFile(os.path.join(out, ZIP_NAME)) as z:
        return json.loads(z.read(f"assets/{namespace}/lang/ru_ru.json"))


class CleanAnswerFromStrongModel(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        make_jar(self.base, "a", {"k1": "Broken one", "k2": "Fine one"})
        self.primary = Model("primary", bad_for("Broken one"))
        self.strong = Model("strong", lambda t: "СИЛЬНЫЙ " + t)
        self.out, self.translator, self.logs = run(self.base, self.primary, self.strong)

    def test_result_is_clean_in_the_pack(self):
        self.assertEqual(pack_lang(self.out, "a"), {"k1": "СИЛЬНЫЙ Broken one", "k2": "ПЕР Fine one"})

    def test_result_is_cached(self):
        self.assertEqual(self.translator.cache.get("Broken one", "ru_ru"), "СИЛЬНЫЙ Broken one")

    def test_strong_model_sees_only_rejected_strings(self):
        self.assertEqual(self.strong.sent, ["Broken one"])

    def test_primary_is_not_asked_again_after_the_pass(self):
        self.assertEqual(sorted(self.primary.sent), ["Broken one", "Fine one"])

    def test_log_says_what_happened(self):
        lines = [x for x in self.logs if x.startswith("[PASS2]")]
        self.assertTrue(any("Исправлено 1 из 1" in x for x in lines), lines)

    def test_next_run_needs_no_model(self):
        path = self.translator.cache.path
        self.translator.flush()
        again = Model("primary", good)
        strong = Model("strong", good)
        run(self.base, again, strong, cache_path=path)
        self.assertEqual(again.sent, [])
        self.assertEqual(strong.sent, [])


class BothModelsFail(unittest.TestCase):
    def test_english_and_not_cached(self):
        base = tempfile.mkdtemp()
        make_jar(base, "a", {"k1": "Broken one", "k2": "Fine one"})
        strong = Model("strong", lambda t: "Сhare")
        out, translator, logs = run(base, Model("primary", bad_for("Broken one")), strong)
        self.assertEqual(pack_lang(out, "a"), {"k2": "ПЕР Fine one"})
        self.assertIsNone(translator.cache.get("Broken one", "ru_ru"))
        self.assertTrue(any("на английском осталось 1" in x for x in logs if x.startswith("[PASS2]")), logs)

    def test_strong_cjk_and_lost_markup_are_rejected_too(self):
        for answer in ("Необходимо至少", "Без маркера"):
            base = tempfile.mkdtemp()
            make_jar(base, "a", {"k": "Broken %s one"})
            strong = Model("strong", lambda t: answer)
            out, translator, _ = run(base, Model("primary", lambda t: BAD), strong)
            self.assertIsNone(translator.cache.get("Broken %s one", "ru_ru"), answer)

    def test_next_run_asks_again(self):
        base = tempfile.mkdtemp()
        make_jar(base, "a", {"k": "Broken one"})
        path = os.path.join(tempfile.mkdtemp(), "c.json")
        _, first, _ = run(base, Model("primary", lambda t: BAD), Model("strong", lambda t: BAD), cache_path=path)
        first.flush()
        primary = Model("primary", lambda t: BAD)
        run(base, primary, Model("strong", lambda t: BAD), cache_path=path)
        self.assertEqual(primary.sent, ["Broken one"])


class WhatCountsAsRejected(unittest.TestCase):
    def _strong_sees(self, reply, text="Source text here"):
        base = tempfile.mkdtemp()
        make_jar(base, "a", {"k": text})
        strong = Model("strong", good)
        run(base, Model("primary", reply), strong)
        return strong.sent

    def test_mixed_script_cjk_markup_and_truncation(self):
        self.assertEqual(self._strong_sees(lambda t: BAD), ["Source text here"])
        self.assertEqual(self._strong_sees(lambda t: "至少"), ["Source text here"])
        self.assertEqual(self._strong_sees(lambda t: LLMTruncatedError("x")), ["Source text here"])
        self.assertEqual(self._strong_sees(lambda t: ""), ["Source text here"])
        self.assertEqual(len(self._strong_sees(lambda t: "Без маркера", text="Source %s text")), 1)

    def test_failures_of_the_connection_do_not_count(self):
        for error in (LLMTimeoutError("t"), LLMClientError("HTTP 500")):
            self.assertEqual(self._strong_sees(lambda t, e=error: e), [], error)

    def test_answer_equal_to_source_does_not_count(self):
        self.assertEqual(self._strong_sees(lambda t: t), [])

    def test_clean_run_does_not_touch_the_strong_model(self):
        base = tempfile.mkdtemp()
        make_jar(base, "a", {"k": "Fine text"})
        asked = []

        def provider():
            asked.append(1)
            return None, ""

        translator = Translator(
            Model("primary", good), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
            strict=True, log=lambda s: None, target_lang="ru_ru",
        )
        logs = []
        with contextlib.redirect_stdout(io.StringIO()):
            mirrorer.mirror_translate_dir(base, tempfile.mkdtemp(), translator, log=logs.append,
                                          target_lang="ru_ru", strong_client=provider)
        self.assertEqual(asked, [])
        self.assertFalse([x for x in logs if x.startswith("[PASS2]")])


class NoStrongModel(unittest.TestCase):
    def test_skip_without_error_with_one_log_line(self):
        base = tempfile.mkdtemp()
        make_jar(base, "a", {"k1": "Broken one", "k2": "Fine one"})
        out, translator, logs = run(base, Model("primary", bad_for("Broken one")), None, reason="причина пропуска")
        self.assertEqual(pack_lang(out, "a"), {"k2": "ПЕР Fine one"})
        lines = [x for x in logs if x.startswith("[PASS2]")]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("причина пропуска", lines[0])
        self.assertIn("1", lines[0])
        self.assertFalse([x for x in logs if x.startswith("[ERR")])


class LooseFiles(unittest.TestCase):
    def test_quest_and_kubejs_files_get_the_fix(self):
        base = tempfile.mkdtemp()
        lang = os.path.join(base, "config/ftbquests/quests/lang")
        kube = os.path.join(base, "kubejs/assets/m/lang")
        os.makedirs(lang)
        os.makedirs(kube)
        with open(os.path.join(lang, "en_us.snbt"), "w", encoding="utf-8") as f:
            f.write('{ quest.A.title: "Broken quest" quest.B.title: "Fine quest" }')
        with open(os.path.join(kube, "en_us.json"), "w", encoding="utf-8") as f:
            json.dump({"a": "Broken lang", "b": "Fine lang"}, f)
        primary = Model("primary", bad_for("Broken quest", "Broken lang"))
        strong = Model("strong", lambda t: "СИЛЬНЫЙ " + t)
        out, _, _ = run(base, primary, strong)
        with open(os.path.join(out, "config/ftbquests/quests/lang/ru_ru.snbt"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("СИЛЬНЫЙ Broken quest", text)
        self.assertIn("ПЕР Fine quest", text)
        with open(os.path.join(out, "kubejs/assets/m/lang/ru_ru.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": "СИЛЬНЫЙ Broken lang", "b": "ПЕР Fine lang"})
        self.assertEqual(sorted(strong.sent), ["Broken lang", "Broken quest"])

    def test_official_values_survive_the_rebuild(self):
        base = tempfile.mkdtemp()
        make_jar(base, "a", {"o": "Official text", "k": "Broken one"}, {"o": "Официальный"})
        out, _, _ = run(base, Model("primary", lambda t: BAD), Model("strong", lambda t: "СИЛЬНЫЙ " + t))
        self.assertEqual(pack_lang(out, "a"), {"k": "СИЛЬНЫЙ Broken one"})


class StopAndPause(unittest.TestCase):
    def test_stop_during_second_pass_leaves_no_partial_output(self):
        base = tempfile.mkdtemp()
        make_jar(base, "a", {f"k{i}": f"Broken number {i}" for i in range(50)})
        cancel = threading.Event()
        ctl = _Control()
        calls = []

        def reply(text):
            calls.append(text)
            if len(calls) == 3:
                ctl.stop()
                cancel.set()
            return "СИЛЬНЫЙ " + text

        strong = Model("strong", reply, delay=0.002)
        path = os.path.join(tempfile.mkdtemp(), "c.json")
        with contextlib.suppress(_StopRequested):
            out, _, logs = run(base, Model("primary", lambda t: BAD), strong, cache_path=path,
                               on_tick=lambda *a: ctl.checkpoint(), cancel=cancel)
        self.assertLess(len(calls), 50)
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)["ru_ru"]
        self.assertGreaterEqual(len(cached), 1, "сделанное сохранено")
        self.assertTrue(all(v.startswith("СИЛЬНЫЙ ") for v in cached.values()))

    def test_stop_gives_no_zip_and_next_run_finishes(self):
        base = tempfile.mkdtemp()
        make_jar(base, "a", {f"k{i}": f"Broken number {i}" for i in range(40)})
        cancel = threading.Event()
        calls = []

        def reply(text):
            calls.append(text)
            if len(calls) == 5:
                cancel.set()
            return "СИЛЬНЫЙ " + text

        path = os.path.join(tempfile.mkdtemp(), "c.json")
        out, _, logs = run(base, Model("primary", lambda t: BAD), Model("strong", reply), cache_path=path, cancel=cancel)
        self.assertEqual(os.listdir(out), [])
        self.assertTrue(any(x.startswith("[STOP]") for x in logs))
        primary, strong = Model("primary", lambda t: BAD), Model("strong", lambda t: "СИЛЬНЫЙ " + t)
        out2, _, _ = run(base, primary, strong, cache_path=path)
        self.assertEqual(len(pack_lang(out2, "a")), 40)
        self.assertLess(len(strong.sent), 40, "готовые строки взяты из кэша")

    def test_pause_holds_strong_requests(self):
        base = tempfile.mkdtemp()
        make_jar(base, "a", {f"k{i}": f"Broken number {i}" for i in range(200)})
        ctl = _Control()
        strong = Model("strong", lambda t: "СИЛЬНЫЙ " + t, delay=0.002)
        translator = Translator(
            Model("primary", lambda t: BAD), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
            strict=True, log=lambda s: None, target_lang="ru_ru", before_request=ctl.wait_if_paused,
        )
        out = tempfile.mkdtemp()

        def work():
            with contextlib.redirect_stdout(io.StringIO()):
                mirrorer.mirror_translate_dir(base, out, translator, log=lambda s: None,
                                              on_tick=lambda *a: ctl.checkpoint(), target_lang="ru_ru",
                                              strong_client=lambda: (strong, ""))

        thread = threading.Thread(target=work)
        thread.start()
        deadline = time.time() + 5
        while not strong.sent and time.time() < deadline:
            time.sleep(0.002)
        ctl.pause()
        time.sleep(0.05)
        before = len(strong.sent)
        time.sleep(0.2)
        during = len(strong.sent) - before
        ctl.resume()
        thread.join(10)
        self.assertEqual(during, 0)
        self.assertEqual(len(strong.sent), 200)

    def test_progress_is_reported_for_the_second_pass(self):
        base = tempfile.mkdtemp()
        make_jar(base, "a", {"k1": "Broken one", "k2": "Broken two"})
        strong = Model("strong", lambda t: BAD if t == "Broken two" else "СИЛЬНЫЙ " + t)
        totals, ticks = [], []
        translator = Translator(
            Model("primary", lambda t: BAD), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")),
            strict=True, log=lambda s: None, target_lang="ru_ru",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            mirrorer.mirror_translate_dir(base, tempfile.mkdtemp(), translator, log=lambda s: None,
                                          on_total=totals.append, on_tick=lambda *a: ticks.append(a),
                                          target_lang="ru_ru", strong_client=lambda: (strong, ""))
        self.assertEqual(totals, [1, 2])
        self.assertEqual(ticks[1:], [(1, 1, 0, 0), (1, 0, 0, 1)])

    def test_dry_run_has_no_second_pass(self):
        base = tempfile.mkdtemp()
        make_jar(base, "a", {"k": "Broken one"})
        strong = Model("strong", good)
        run(base, Model("primary", lambda t: BAD), strong, write=False)
        self.assertEqual(strong.sent, [])


class CacheOnly(unittest.TestCase):
    def test_no_requests_and_source_for_missing(self):
        client = Model("primary", good)
        tr = Translator(client, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                        log=lambda s: None, target_lang="ru_ru")
        tr.cache.put("Known", "Известно", "ru_ru")
        tr.cache_only = True
        self.assertEqual(tr.translate("Known"), "Известно")
        self.assertEqual(tr.translate("Unknown text"), "Unknown text")
        self.assertEqual(tr.translate_many(["Known", "Other text"]), ["Известно", "Other text"])
        self.assertEqual(client.sent, [])
        self.assertEqual(tr.take_rejected(), [])


class ChoosingTheModel(unittest.TestCase):
    def _cfg(self, **kw):
        values = dict(mode="local", tier="light", strong_model="llama3.1:8b-instruct-q4_K_M",
                      ollama_base_url="http://localhost:11434", ollama_model="", standard_model_id="",
                      light_model_id="", prompt_cache=True, local_server_url="", local_model_path="")
        values.update(kw)
        return types.SimpleNamespace(**values)

    def _ollama(self, *names, available=True):
        status = ollama_probe.OllamaStatus(
            available=available, base_url="http://localhost:11434",
            models=[ollama_probe.OllamaModel(name=n) for n in names],
        )
        return mock.patch.object(ollama_probe, "probe_ollama", return_value=status)

    def _standard(self, downloaded):
        return mock.patch.object(factory.downloader, "is_downloaded", return_value=downloaded)

    def test_ollama_with_the_chosen_model_comes_first(self):
        with self._ollama("llama3.1:8b-instruct-q4_K_M", "other:latest"), self._standard(True):
            client, _ = factory.build_strong_client(self._cfg())
        self.assertEqual(client.model, "llama3.1:8b-instruct-q4_K_M")

    def test_short_name_matches_installed_tag(self):
        with self._ollama("llama3.1:8b-instruct-q4_K_M"), self._standard(False):
            client, _ = factory.build_strong_client(self._cfg(strong_model="llama3.1:8b"))
        self.assertEqual(client.model, "llama3.1:8b-instruct-q4_K_M")

    def test_other_chosen_model_is_used(self):
        with self._ollama("mymodel:latest"), self._standard(False):
            client, _ = factory.build_strong_client(self._cfg(strong_model="mymodel"))
        self.assertEqual(client.model, "mymodel:latest")

    def test_falls_back_to_downloaded_standard(self):
        with self._ollama("other:latest"), self._standard(True):
            client, _ = factory.build_strong_client(self._cfg())
        self.assertIn("7b", client.model.lower())
        self.assertTrue(client.model_path)

    def test_no_ollama_no_standard_gives_reason_and_downloads_nothing(self):
        with self._ollama(available=False), self._standard(False), \
                mock.patch.object(factory.downloader, "download_model", create=True) as download:
            client, reason = factory.build_strong_client(self._cfg())
        self.assertIsNone(client)
        self.assertIn("llama3.1", reason)
        download.assert_not_called()

    def test_primary_ollama_already_strong_means_skip(self):
        with self._ollama("llama3.1:8b-instruct-q4_K_M"), self._standard(True):
            client, reason = factory.build_strong_client(
                self._cfg(mode="ollama", ollama_model="llama3.1:8b-instruct-q4_K_M"))
        self.assertIsNone(client)
        self.assertIn("уже", reason)

    def test_external_provider_is_skipped_and_gets_no_paid_second_pass(self):
        client, reason = factory.build_strong_client(self._cfg(mode="external"))
        self.assertIsNone(client)
        self.assertTrue(reason)

    def test_off_disables_the_pass(self):
        with self._ollama("llama3.1:8b-instruct-q4_K_M"), self._standard(True):
            client, reason = factory.build_strong_client(self._cfg(strong_model="off"))
        self.assertIsNone(client)
        self.assertIn("отключ", reason)

    def test_setting_exists_and_default_model_is_not_changed(self):
        defaults = config.ProviderConfig()
        self.assertEqual(defaults.strong_model, "llama3.1:8b-instruct-q4_K_M")
        self.assertEqual((defaults.mode, defaults.tier), ("local", "light"))


class ApiWiring(unittest.TestCase):
    def test_real_api_runs_the_second_pass(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        make_jar(base, "a", {"k": "Broken one"})
        primary = Model("primary", lambda t: BAD)
        strong = Model("strong", lambda t: "СИЛЬНЫЙ " + t)
        cache_path = os.path.join(tempfile.mkdtemp(), "c.json")
        with mock.patch.object(factory, "build_clients", return_value=(primary, None)), \
                mock.patch.object(factory, "build_strong_client", return_value=(strong, "")), \
                mock.patch.object(config, "DEFAULT_CACHE_PATH", cache_path), \
                mock.patch.object(config.PROVIDER, "ollama_model", "m"):
            api = Api()
            api._set(input=base, output=out, dry=False, mode="ollama")
            api.start()
            api._worker.join(10)
        self.assertEqual(api._state["phase"], "done")
        self.assertEqual(pack_lang(out, "a"), {"k": "СИЛЬНЫЙ Broken one"})


if __name__ == "__main__":
    unittest.main()
