"""Длинный прогон переживает остановку: кэш пишется по ходу, продолжение без повторов, пак из кэша."""
from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import zipfile

from src import mirrorer
from src.processors.jar_lang import is_priority_key
from src.translators import Translator
from src.utils.cache import TranslationCache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LONG = "It climbs trees by hooking on with its sharp claws and shares the berries it gathers with others"


def make_pack(base, n=30):
    os.makedirs(os.path.join(base, "mods"), exist_ok=True)
    for j in range(2):
        data = {}
        for i in range(n):
            data[f"item.m{j}.thing{i}"] = f"Thing {j} {i}"
            data[f"item.m{j}.thing{i}.desc"] = f"{LONG}, variant {j} {i}."
        with zipfile.ZipFile(os.path.join(base, "mods", f"m{j}.jar"), "w") as z:
            z.writestr(f"assets/m{j}/lang/en_us.json", json.dumps(data))
    return 4 * n


class Model:
    name = "m"

    def __init__(self, stop_after=None, stop=None, reject=()):
        self.seen = []
        self.stop_after, self.stop, self.reject = stop_after, stop, set(reject)
        self.lock = threading.Lock()

    def chat(self, messages, **kwargs):
        text = messages[-1]["content"]
        with self.lock:
            self.seen.append(text)
            if self.stop_after is not None and len(self.seen) >= self.stop_after:
                self.stop.set()
        if text in self.reject:
            return "Плохой перевод[ru]"
        return "Перевод " + str(abs(hash(text)) % 100000)


def run(base, cache_path, model, cancel=None, strong=None):
    out = tempfile.mkdtemp()
    tr = Translator(model, TranslationCache(cache_path), strict=True, log=lambda s: None, target_lang="ru_ru",
                    cancel_event=cancel)
    tr.max_attempts = 1
    with contextlib.redirect_stdout(io.StringIO()):
        mirrorer.mirror_translate_dir(base, out, tr, log=lambda s: None, target_lang="ru_ru", cancel_event=cancel,
                                      strong_client=(lambda: (strong, "")) if strong else None)
    zips = [n for n in os.listdir(out) if n.endswith(".zip")]
    return tr, (os.path.join(out, zips[0]) if zips else None)


def pack_strings(path):
    with zipfile.ZipFile(path) as z:
        return sum(len(json.loads(z.read(n))) for n in z.namelist() if n.endswith("/ru_ru.json"))


class StopAndResume(unittest.TestCase):
    def test_stop_then_continue_without_repeats(self):
        base, cache = tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "c.json")
        total = make_pack(base)
        stop = threading.Event()
        first = Model(stop_after=25, stop=stop)
        _, zip_path = run(base, cache, first, cancel=stop)
        self.assertIsNone(zip_path)
        with open(cache, encoding="utf-8") as f:
            saved = json.load(f)["ru_ru"]
        self.assertGreaterEqual(len(saved), 20)
        second = Model()
        _, zip_path = run(base, cache, second)
        self.assertEqual(set(first.seen) & set(second.seen), set())
        self.assertEqual(len(first.seen) + len(second.seen), total)
        self.assertEqual(pack_strings(zip_path), total)

    def test_killed_process_keeps_what_was_saved(self):
        base, cache = tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "c.json")
        total = make_pack(base, n=200)
        script = textwrap.dedent(f"""
            import sys, time, io, contextlib
            sys.path.insert(0, {ROOT!r})
            from src import mirrorer
            from src.translators import Translator
            from src.utils.cache import TranslationCache
            class Slow:
                name = "slow"
                def chat(self, messages, **kwargs):
                    time.sleep(0.01)
                    return "Перевод " + str(abs(hash(messages[-1]["content"])) % 100000)
            tr = Translator(Slow(), TranslationCache({cache!r}, save_interval=0.2), strict=True, log=lambda s: None, target_lang="ru_ru")
            with contextlib.redirect_stdout(io.StringIO()):
                mirrorer.mirror_translate_dir({base!r}, {tempfile.mkdtemp()!r}, tr, log=lambda s: None, target_lang="ru_ru")
        """)
        proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 60
        saved = {}
        while time.time() < deadline:
            time.sleep(0.2)
            try:
                with open(cache, encoding="utf-8") as f:
                    saved = json.load(f).get("ru_ru", {})
            except (OSError, ValueError):
                continue
            if len(saved) >= 100:
                break
        proc.send_signal(signal.SIGKILL)
        proc.wait(10)
        with open(cache, encoding="utf-8") as f:
            saved = json.load(f)["ru_ru"]
        self.assertGreaterEqual(len(saved), 100)
        self.assertLess(len(saved), total)
        model = Model()
        _, zip_path = run(base, cache, model)
        self.assertEqual(set(model.seen) & set(saved), set())
        self.assertEqual(len(model.seen), total - len(saved))
        self.assertEqual(pack_strings(zip_path), total)

    def test_rejected_strings_skip_the_fast_model_after_restart(self):
        base, cache = tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "c.json")
        make_pack(base, n=5)
        bad = {"Thing 0 1", "Thing 1 2"}
        # Первый прогон остановлен после основной модели, до второго прохода.
        stop = threading.Event()
        first_run, _ = run(base, cache, Model(reject=bad, stop_after=20, stop=stop), cancel=stop, strong=Model())
        with open(cache + ".pending.json", encoding="utf-8") as f:
            self.assertEqual(set(json.load(f)["ru_ru"]), bad)
        fast, strong = Model(), Model()
        _, zip_path = run(base, cache, fast, strong=strong)
        self.assertEqual(set(fast.seen) & bad, set())
        self.assertEqual(set(strong.seen), bad)
        self.assertFalse(os.path.exists(cache + ".pending.json"))


class PriorityOrder(unittest.TestCase):
    def test_names_of_all_mods_come_before_descriptions(self):
        base, cache = tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "c.json")
        make_pack(base, n=5)
        model = Model()
        run(base, cache, model)
        kinds = ["desc" if text.startswith("It climbs") else "name" for text in model.seen]
        self.assertEqual(kinds, ["name"] * 10 + ["desc"] * 10)

    def test_priority_rule(self):
        self.assertTrue(is_priority_key("item.cobblemon.poke_ball", "Poké Ball"))
        self.assertTrue(is_priority_key("gui.menu.options", "Options"))
        self.assertTrue(is_priority_key("tip.cobblemontips.apricorns.text", LONG))
        self.assertFalse(is_priority_key("cobblemon.species.bulbasaur.desc", "A seed on its back."))
        self.assertFalse(is_priority_key("item.cobblemon.link_cable.tooltip", "Evolves pokemon"))
        self.assertFalse(is_priority_key("item.x.y", LONG))


class BuildFromCache(unittest.TestCase):
    def test_partial_cache_gives_a_partial_pack_without_a_model(self):
        from unittest import mock
        from src import config
        from src.gui.api import Api
        base, cache = tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "c.json")
        make_pack(base, n=5)
        stop = threading.Event()
        run(base, cache, Model(stop_after=7, stop=stop), cancel=stop)
        with open(cache, encoding="utf-8") as f:
            saved = len(json.load(f)["ru_ru"])
        out = tempfile.mkdtemp()
        api = Api()
        api._state["input"], api._state["output"] = base, out
        with mock.patch.object(config, "DEFAULT_CACHE_PATH", cache):
            api._run_build_from_cache(base, out, "ru_ru")
        zips = [n for n in os.listdir(out) if n.endswith(".zip")]
        self.assertEqual(len(zips), 1)
        self.assertEqual(pack_strings(os.path.join(out, zips[0])), saved)
        self.assertTrue(any("[PACK]" in line for line in api._logs))

    def test_during_a_run_the_pack_goes_to_partial(self):
        from unittest import mock
        from src.gui.api import Api
        base = tempfile.mkdtemp()
        make_pack(base, n=1)
        api = Api()
        api._state["input"], api._state["output"], api._state["running"] = base, tempfile.mkdtemp(), True
        with mock.patch("threading.Thread") as thread:
            api.build_from_cache()
        self.assertTrue(thread.call_args.kwargs["args"][1].endswith("partial"))

    def test_finished_run_without_strong_model_asks_again_next_time(self):
        base, cache = tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "c.json")
        make_pack(base, n=2)
        run(base, cache, Model(reject={"Thing 0 1"}))
        self.assertFalse(os.path.exists(cache + ".pending.json"))
        again = Model()
        run(base, cache, again)
        self.assertIn("Thing 0 1", again.seen)

    def test_repeat_inside_one_run_is_not_asked_twice(self):
        base, cache = tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "c.json")
        os.makedirs(os.path.join(base, "mods"))
        for j in range(3):
            with zipfile.ZipFile(os.path.join(base, "mods", f"r{j}.jar"), "w") as z:
                z.writestr(f"assets/r{j}/lang/en_us.json", json.dumps({f"item.r{j}.x": "Broken Thing"}))
        model = Model(reject={"Broken Thing"})
        run(base, cache, model)
        self.assertEqual(model.seen.count("Broken Thing"), 1)

    def test_build_does_not_touch_the_pending_list(self):
        cache = os.path.join(tempfile.mkdtemp(), "c.json")
        with open(cache + ".pending.json", "w", encoding="utf-8") as f:
            json.dump({"ru_ru": ["Thing"]}, f)
        tr = Translator(Model(), TranslationCache(cache), log=lambda s: None, target_lang="ru_ru")
        tr.cache_only = True
        tr._pending_open.clear()
        tr.flush()
        self.assertTrue(os.path.exists(cache + ".pending.json"))


if __name__ == "__main__":
    unittest.main()
