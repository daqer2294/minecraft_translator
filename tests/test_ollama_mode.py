"""
Режим «Локальная Ollama» без llama_cpp, как на ПК с несработавшей CUDA-сборкой:
основная модель qwen2.5:3b, длинные тексты и второй проход llama3.1:8b, обе в Ollama.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from src import config, speed_test
from src.gui.api import Api
from src.llm import factory, ollama_probe
from src.llm import hardware_probe as hp
from src.llm import model_downloader
from tests.fake_ollama import MAIN, STRONG, FakeOllama
from tests.test_win_dll import without_llama_cpp

LONG = "Right click the machine with a wrench to rotate it, and shift right click to pick it up again."
RU = {
    "Iron Ingot": "Железный слиток",
    "Gold Ingot": "Золотой слиток",
    "Copper Wire": "Медный провод",
    LONG: "Щёлкните по машине гаечным ключом, чтобы повернуть её, а с зажатой клавишей — чтобы снова забрать.",
}
BAD = "Мinecraft"  # смесь алфавитов: валидатор отвергает, строка уходит во второй проход


def reply(model, text):
    if model == MAIN and text == "Copper Wire":
        return BAD
    return RU.get(text, "Перевод")


def make_jar(base):
    mods = os.path.join(base, "mods")
    os.makedirs(mods)
    en = {"item.a.iron": "Iron Ingot", "item.a.gold": "Gold Ingot", "item.a.wire": "Copper Wire", "tip.a.long": LONG}
    with zipfile.ZipFile(os.path.join(mods, "a.jar"), "w") as z:
        z.writestr("assets/a/lang/en_us.json", json.dumps(en))


class OllamaMode(unittest.TestCase):
    def setUp(self):
        without_llama_cpp(self)
        self.ollama = FakeOllama(reply=reply)
        self.addCleanup(self.ollama.close)
        self.cfg = mock.patch.multiple(config.PROVIDER, ollama_base_url=self.ollama.url, ollama_model="",
                                       strong_model=STRONG, mode="local", gpu_profile="auto")
        self.cfg.start()
        self.addCleanup(self.cfg.stop)

    def test_app_translates_and_runs_the_second_pass_through_ollama(self):
        base, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        make_jar(base)
        with mock.patch.object(config, "DEFAULT_CACHE_PATH", os.path.join(tempfile.mkdtemp(), "c.json")), \
             contextlib.redirect_stdout(io.StringIO()):
            api = Api()
            api.detect_ollama()                      # как при выборе «Локальная Ollama»
            self.assertEqual(config.PROVIDER.ollama_model, MAIN)
            api.set_ollama_model(MAIN)               # кнопка «Использовать»
            api._set(input=base, output=out, dry=False)
            api.start()
            api._worker.join(30)
        self.assertEqual(api._state["phase"], "done", api._logs[-5:])
        self.assertEqual(api._state["mode"], "ollama")
        with zipfile.ZipFile(os.path.join(out, "MinecraftTranslator_ru_ru.zip")) as z:
            lang = json.loads(z.read("assets/a/lang/ru_ru.json"))
        self.assertEqual(lang, {"item.a.iron": RU["Iron Ingot"], "item.a.gold": RU["Gold Ingot"],
                                "item.a.wire": RU["Copper Wire"], "tip.a.long": RU[LONG]})
        main, strong = self.ollama.texts_for(MAIN), self.ollama.texts_for(STRONG)
        self.assertEqual(sorted(set(main)), ["Copper Wire", "Gold Ingot", "Iron Ingot"])
        self.assertIn(LONG, strong)                  # от 15 слов сразу сильной модели
        self.assertNotIn(LONG, main)
        self.assertIn("Copper Wire", strong)         # брак основной модели: второй проход
        self.assertEqual({m for m, _ in self.ollama.sent}, {MAIN, STRONG})
        self.assertTrue(any("llama_cpp не загрузилась" in line for line in api._logs))

    def test_main_model_is_the_chosen_one_and_qwen_by_default(self):
        names = [STRONG, "gemma2:2b", MAIN]
        self.assertEqual(ollama_probe.suggest_model([ollama_probe.OllamaModel(n) for n in names]), MAIN)
        self.assertEqual(speed_test.ollama_main_model(names), MAIN)
        self.assertEqual(speed_test.ollama_main_model(names, STRONG), STRONG)
        self.assertIsNone(speed_test.ollama_main_model(names, "qwen2.5:7b"))
        self.assertEqual(ollama_probe.recommended_pull_cmd(), "ollama pull qwen2.5:3b")
        self.assertEqual(config.ProviderConfig().strong_model, STRONG)

    def test_main_equal_to_strong_means_no_second_model(self):
        cfg = config.ProviderConfig(mode="ollama", ollama_base_url=self.ollama.url, ollama_model=STRONG,
                                    strong_model=STRONG)
        client, reason = factory.build_strong_client(cfg)
        self.assertIsNone(client)
        self.assertIn("Основная модель уже", reason)

    def test_no_gguf_fallback_when_llama_cpp_does_not_load(self):
        self.ollama.models = [MAIN]
        cfg = config.ProviderConfig(mode="ollama", ollama_base_url=self.ollama.url, ollama_model=MAIN,
                                    strong_model=STRONG)
        with mock.patch.object(model_downloader, "is_downloaded", return_value=True):
            client, reason = factory.build_strong_client(cfg)
        self.assertIsNone(client)
        self.assertIn("llama_cpp не загрузилась", reason)
        self.assertIn(f"ollama pull {STRONG}", reason)


class OllamaSpeedProfile(unittest.TestCase):
    def setUp(self):
        without_llama_cpp(self)
        self.ollama = FakeOllama(reply=reply)
        self.addCleanup(self.ollama.close)
        patcher = mock.patch.multiple(config.PROVIDER, ollama_base_url=self.ollama.url, ollama_model="",
                                      strong_model=STRONG, mode="local")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.home = tempfile.mkdtemp()
        p = mock.patch.object(config, "APP_DATA_DIR", self.home)
        p.start()
        self.addCleanup(p.stop)

    def test_profile_measures_short_and_long_separately(self):
        strings = ["Iron Ingot", "Gold Ingot", "Copper Wire", LONG]
        with mock.patch.object(speed_test.subprocess, "run", side_effect=FileNotFoundError()):
            r = speed_test.run_ollama_profile(strings, "")
        self.assertEqual((r["main_model"], r["strong_model"]), (MAIN, STRONG))
        self.assertEqual((r["short_model"], r["long_model"]), (MAIN, STRONG))
        self.assertEqual((r["short_strings"], r["long_strings"]), (3, 1))
        self.assertEqual((r["short_translated"], r["long_translated"]), (2, 1))
        self.assertEqual((r["to_second_pass"], r["fixed_by_second_pass"]), (1, 1))
        self.assertGreater(r["short_strings_per_minute"], 0)
        self.assertGreater(r["long_strings_per_minute"], 0)
        self.assertNotIn(LONG, self.ollama.texts_for(MAIN))

    def run_report(self, *args):
        argv = ["x", "--speed-test", *args]
        with mock.patch.object(speed_test.subprocess, "run", side_effect=FileNotFoundError()), \
             mock.patch.object(speed_test, "bundled_strings", return_value=["Iron Ingot", "Gold Ingot", LONG, LONG + " Now."]), \
             mock.patch.object(hp, "probe_hardware", return_value=hp.HardwareInfo(True, "NVIDIA GeForce RTX 4060 Ti", 8188, 32000, 14, "standard", 0.0)):
            path = speed_test.cli(argv)
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_report_without_llama_cpp_skips_local_profiles_and_runs_ollama(self):
        text = self.run_report()
        self.assertIn("Перевод: локальная модель недоступна (llama_cpp: FileNotFoundError: Could not find module", text)
        self.assertIn("Почему llama_cpp не загрузилась:", text)
        self.assertIn("[auto] Как обычно", text)
        self.assertIn("[auto] Как обычно: основная модель на видеокарте, если она есть: пропущено, встроенная модель не работает", text)
        self.assertIn("[fast_cpu]", text)
        self.assertNotIn("TypeError", text)
        self.assertIn(f"[ollama] {speed_test.OLLAMA_TITLE}", text)
        self.assertIn(f"  main_model: {MAIN}", text)
        self.assertIn(f"  long_model: {STRONG}", text)
        self.assertIn("  short_strings_per_minute: ", text)
        self.assertIn("  long_strings_per_minute: ", text)

    def test_only_ollama_and_chosen_model(self):
        text = self.run_report("--profile", "ollama", "--ollama-model", STRONG)
        self.assertNotIn("[auto]", text)
        self.assertNotIn("Сильная модель в потоках", text)
        self.assertIn(f"  main_model: {STRONG}", text)
        self.assertIn("  strong_model: нет: Основная модель уже", text)

    def test_ollama_down_is_reported_not_crashed(self):
        self.ollama.close()
        text = self.run_report("--profile", "ollama")
        self.assertIn("[ollama] Только Ollama", text)
        self.assertIn("пропущено, Ollama не отвечает", text)

    def test_unknown_profile(self):
        with self.assertRaises(SystemExit):
            speed_test.cli(["x", "--speed-test", "--profile", "turbo"])


if __name__ == "__main__":
    unittest.main()
