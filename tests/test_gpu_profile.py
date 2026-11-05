"""Профиль под 8 ГБ: основная модель на процессоре, длинные тексты сильной модели одновременно."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

from src import config, mirrorer
from src.llm import factory
from src.translators import Translator
from src.utils.cache import TranslationCache

LONG = "It climbs trees by hooking on with its sharp claws and shares the berries it gathers with others."
LONG2 = "Its skin is thick enough to fend off the spikes, and with its robust teeth it crunches up spikes and eats them."


class Model:
    def __init__(self, name, delay=0.0):
        self.name, self.delay = name, delay
        self.threads, self.seen = [], []
        self.lock = threading.Lock()

    def chat(self, messages, **kwargs):
        time.sleep(self.delay)
        with self.lock:
            self.threads.append(threading.current_thread().name)
            self.seen.append(messages[-1]["content"])
        return {"fast": "Основной перевод", "strong": "Сильный перевод"}[self.name] + " " + str(len(self.seen))


def run(overlap, fast, strong):
    base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
    os.makedirs(os.path.join(base, "mods"))
    data = {"a": "Iron Ingot", "long1": LONG, "b": "Gold Ingot", "long2": LONG2, "c": "Copper Ingot"}
    with zipfile.ZipFile(os.path.join(base, "mods", "m.jar"), "w") as z:
        z.writestr("assets/m/lang/en_us.json", json.dumps(data))
    tr = Translator(fast, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                    log=lambda s: None, target_lang="ru_ru", long_text_client=lambda: (strong, ""))
    tr.max_attempts = 1
    tr.overlap_strong = overlap
    with contextlib.redirect_stdout(io.StringIO()):
        mirrorer.mirror_translate_dir(base, out, tr, log=lambda s: None, target_lang="ru_ru")
    name = [n for n in os.listdir(out) if n.endswith(".zip")][0]
    with zipfile.ZipFile(os.path.join(out, name)) as z:
        return json.loads(z.read("assets/m/lang/ru_ru.json"))


class Overlap(unittest.TestCase):
    def test_long_texts_run_in_another_thread_and_order_is_kept(self):
        fast, strong = Model("fast", 0.02), Model("strong", 0.05)
        pack = run(True, fast, strong)
        self.assertEqual(list(pack), ["a", "long1", "b", "long2", "c"])
        self.assertTrue(all(name.startswith("strong") for name in strong.threads))
        self.assertFalse(any(name.startswith("strong") for name in fast.threads))
        self.assertEqual(sorted(strong.seen), sorted([LONG, LONG2]))
        self.assertEqual(len(fast.seen), 3)

    def test_overlap_saves_time_when_models_do_not_share_a_device(self):
        t = time.perf_counter()
        run(False, Model("fast", 0.1), Model("strong", 0.1))
        sequential = time.perf_counter() - t
        t = time.perf_counter()
        run(True, Model("fast", 0.1), Model("strong", 0.1))
        overlapped = time.perf_counter() - t
        self.assertLess(overlapped, sequential - 0.1)

    def test_same_answers_with_and_without_overlap(self):
        a = run(False, Model("fast"), Model("strong"))
        b = run(True, Model("fast"), Model("strong"))
        self.assertEqual({k: v.split()[0] for k, v in a.items()}, {k: v.split()[0] for k, v in b.items()})
        self.assertEqual(set(a), set(b))

    def test_off_by_default(self):
        tr = Translator(Model("fast"), TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), log=lambda s: None)
        self.assertFalse(tr.overlap_strong)


class Profile(unittest.TestCase):
    def test_fast_cpu_puts_the_fast_model_on_the_processor(self):
        cfg = config.ProviderConfig(gpu_profile="fast_cpu", local_model_path="/x.gguf")
        self.assertEqual(factory.fast_gpu_layers(cfg), 0)
        self.assertEqual(factory._make_local_light(config.ProviderConfig(gpu_profile="fast_cpu")).n_gpu_layers, 0)

    def test_auto_keeps_the_old_choice(self):
        self.assertIsNone(factory.fast_gpu_layers(config.ProviderConfig()))
        self.assertEqual(config.ProviderConfig().gpu_profile, "auto")

    def test_api_sets_the_profile_and_rejects_unknown(self):
        from src.gui.api import Api
        api = Api()
        old = config.PROVIDER.gpu_profile
        try:
            self.assertEqual(api.set_gpu_profile("fast_cpu")["gpu_profile"], "fast_cpu")
            self.assertEqual(config.PROVIDER.gpu_profile, "fast_cpu")
            api.set_gpu_profile("nonsense")
            self.assertEqual(config.PROVIDER.gpu_profile, "fast_cpu")
        finally:
            config.PROVIDER.gpu_profile = old


if __name__ == "__main__":
    unittest.main()
