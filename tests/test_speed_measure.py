"""
Замер полного конвейера в тесте скорости: откаты по причинам и по типу строки, второй проход,
загрузка моделей до замера, одновременный режим fast_cpu и оценка времени на сборку.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

from src import config, speed_test
from src.llm.base import LLMClientError
from src.llm.local_llamacpp import LocalLlamaCppClient
from src.translators import Translator
from src.utils.cache import TranslationCache

LONG = "It climbs trees by hooking on with its sharp claws and shares the berries it gathers with others."
LONG_RU = "Он лазит по деревьям, цепляясь острыми когтями, и делится собранными ягодами с другими."
BAD = "Мinecraft"


class Model:
    """reply(текст) -> ответ или исключение. delay — пауза на запрос."""

    def __init__(self, name, reply, delay=0.0):
        self.name = self.model = name
        self.reply = reply
        self.delay = delay
        self.sent = []
        self.lock = threading.Lock()

    def chat(self, messages, **kwargs):
        user = messages[-1]["content"]
        if self.delay:
            time.sleep(self.delay)
        if "JSON array" in messages[0]["content"]:
            items = json.loads(user)
            with self.lock:
                self.sent += items
            return json.dumps([self.reply(x) for x in items], ensure_ascii=False)
        with self.lock:
            self.sent.append(user)
        answer = self.reply(user)
        if isinstance(answer, Exception):
            raise answer
        return answer


def primary_reply(text):
    return {
        "Hi": "Привет",
        "Iron Ingot": "Железный слиток",
        "Copper Wire": BAD,                                   # смесь алфавитов
        "Gold Ingot": LLMClientError("модель упала"),         # ошибка модели
        "Cobblemon": "Cobblemon",                             # вернула как есть
    }.get(text, "Перевод")


def strong_reply(text):
    return {"Hi": "Привет", "Copper Wire": "Медный провод", LONG: LONG_RU}.get(text, LONG_RU + " " + BAD)


def translator(primary, strong):
    return Translator(primary, TranslationCache(os.path.join(tempfile.mkdtemp(), "c.json")), strict=True,
                      log=lambda s: None, target_lang="ru_ru", long_text_client=lambda: (strong, ""))


def run(strings, primary, strong, parallel=False):
    tr = translator(primary, strong)
    tr.max_attempts = 1
    return speed_test.measure(tr, strings, primary, strong, "", lambda: (strong, ""), parallel=parallel)


class FullPipeline(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "BATCH_SIZE", 1)
        p.start()
        self.addCleanup(p.stop)

    def test_reasons_types_and_second_pass(self):
        long_bad = LONG + " And then some more words."
        strings = ["Iron Ingot", "Copper Wire", "Gold Ingot", "Cobblemon", "minecraft:iron_ingot", LONG, long_bad]
        primary, strong = Model("qwen", primary_reply), Model("llama", strong_reply)
        r = run(strings, primary, strong)
        self.assertEqual((r["short_strings"], r["long_strings"]), (5, 2))
        self.assertEqual((r["short_translated_first_pass"], r["long_translated_first_pass"]), (1, 1))
        self.assertEqual(r["rolled_back_first_pass"], 5)
        self.assertEqual(r["rollback_reasons"], {
            "mixed_script": {"short": 1, "long": 1, "to_second_pass": 1},    # длинную сильная уже не исправит
            "error": {"short": 1, "long": 0, "to_second_pass": 0},           # сбой связи во второй проход не идёт
            "unchanged": {"short": 1, "long": 0, "to_second_pass": 0},
            "rule_skip": {"short": 1, "long": 0, "to_second_pass": 0},
        })
        self.assertEqual((r["to_second_pass"], r["fixed_by_second_pass"]), (1, 1))
        self.assertEqual((r["short_translated_final"], r["long_translated_final"], r["translated_final"]), (2, 1, 3))
        self.assertEqual(r["model_errors"], {"LLMClientError: модель упала": 1})
        self.assertIn("Copper Wire", strong.sent)
        self.assertNotIn(LONG, primary.sent)
        text = "\n".join(speed_test.format_result(r))
        self.assertIn("    смесь алфавитов в слове: 1 / 1, во второй проход 1", text)
        self.assertIn("    ошибка модели или связи: 1 / 0, во второй проход 0", text)
        self.assertIn("  Ошибка модели (1 раз): LLMClientError: модель упала", text)

    def test_seventy_short_errors_give_zero_to_second_pass(self):
        """Картина с ПК: короткие все откатились, во второй проход ничего, длинные перевела сильная."""
        primary = Model("qwen", lambda t: "Привет" if t == "Hi" else LLMClientError("GGUF-модель не найдена"))
        strong = Model("llama", lambda t: LONG_RU if t.startswith(LONG) else "Привет")
        strings = [f"Item {chr(65 + i)}" for i in range(7)] + [LONG + f" Part {chr(65 + i)}." for i in range(3)]
        r = run(strings, primary, strong)
        self.assertEqual((r["translated_first_pass"], r["rolled_back_first_pass"], r["to_second_pass"]), (3, 7, 0))
        self.assertEqual(r["rollback_reasons"], {"error": {"short": 7, "long": 0, "to_second_pass": 0}})
        self.assertEqual(r["model_errors"], {"LLMClientError: GGUF-модель не найдена": 7})
        self.assertIn("не считается: в замере были ошибки модели", "\n".join(
            speed_test.pack_estimate(r, {"pack": "ATM10", "how": "тест", "short": 100, "long": 10})))

    def test_model_load_is_outside_the_measured_time(self):
        loads = []

        def slow_first(text):
            if text == "Hi":
                loads.append(text)
                time.sleep(0.5)
                return "Привет"
            return "Железный слиток"

        r = run(["Iron Ingot"], Model("qwen", slow_first), Model("llama", strong_reply))
        self.assertEqual(loads, ["Hi"])
        self.assertGreaterEqual(r["load_seconds_main"], 0.5)
        self.assertLess(r["seconds_total"], 0.5)

    def test_main_model_that_does_not_load_fails_the_profile(self):
        missing = LocalLlamaCppClient(model_path=os.path.join(tempfile.mkdtemp(), "нет.gguf"))
        with self.assertRaises(RuntimeError) as ctx:
            run(["Iron Ingot"], missing, Model("llama", strong_reply))
        self.assertIn("Основная модель", str(ctx.exception))
        self.assertIn("GGUF-модель не найдена", str(ctx.exception))

    def test_report_shows_the_failed_profile(self):
        home = tempfile.mkdtemp()
        missing = LocalLlamaCppClient(model_path=os.path.join(tempfile.mkdtemp(), "нет.gguf"))
        with mock.patch.object(config, "APP_DATA_DIR", home), \
             mock.patch.object(speed_test.factory, "build_clients", return_value=(missing, None)), \
             mock.patch.object(speed_test.factory, "shared_strong_client", return_value=lambda: (None, "нет")), \
             mock.patch.object(speed_test.hp, "gpu_offload_supported", return_value=True), \
             mock.patch.object(speed_test, "strong_threads", return_value=[]), \
             mock.patch.object(speed_test.hp, "get_or_probe", return_value=speed_test.hp.HardwareInfo(True, "NVIDIA GeForce RTX 4060 Ti", 8188, 32000, 14, "standard", 0.0)), \
             mock.patch.object(speed_test.subprocess, "run", side_effect=FileNotFoundError()):
            path = speed_test.report(None, limit=4, profiles=("auto",))
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("[auto] ОШИБКА: RuntimeError: Основная модель", text)
        self.assertIn("GGUF-модель не найдена", text)
        self.assertNotIn("rolled_back_first_pass", text)

    def test_report_takes_the_hardware_probe_like_the_app(self):
        """Без сохранённой пробы основная модель на Windows шла бы на процессор (n_gpu_layers 0)."""
        home = tempfile.mkdtemp()
        primary, strong = Model("qwen", primary_reply), Model("llama", strong_reply)
        primary.n_gpu_layers = -1
        with mock.patch.object(config, "APP_DATA_DIR", home), \
             mock.patch.object(speed_test.factory, "build_clients", return_value=(primary, None)), \
             mock.patch.object(speed_test.factory, "shared_strong_client", return_value=lambda: (strong, "")), \
             mock.patch.object(speed_test.hp, "gpu_offload_supported", return_value=True), \
             mock.patch.object(speed_test, "strong_threads", return_value=[]), \
             mock.patch.object(speed_test.subprocess, "run", side_effect=FileNotFoundError()), \
             mock.patch.object(speed_test.hp, "get_or_probe", return_value=speed_test.hp.HardwareInfo(True, "NVIDIA GeForce RTX 4060 Ti", 8188, 32000, 14, "standard", 0.0)) as probe:
            path = speed_test.report(None, limit=4, profiles=("auto",))
        probe.assert_called_once_with()
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("  main_gpu_layers: -1", text)
        self.assertIn("  Оценка ATM10 8.4", text)

    def test_parallel_parts_overlap_like_fast_cpu(self):
        strings = ["Iron Ingot", "Iron Ingot two", LONG, LONG + " Again."]
        primary = Model("qwen", lambda t: "Привет" if t == "Hi" else "Железный слиток", delay=0.3)
        strong = Model("llama", lambda t: "Привет" if t == "Hi" else LONG_RU, delay=0.3)
        serial = run(strings, primary, strong)
        parallel = run(strings, primary, strong, parallel=True)
        self.assertGreaterEqual(serial["seconds_first_pass"], 1.1)
        self.assertLess(parallel["seconds_first_pass"], 0.9)
        self.assertTrue(parallel["parallel_parts"])
        self.assertEqual(parallel["translated_final"], 4)


class PackEstimate(unittest.TestCase):
    R = {"short_strings": 70, "short_seconds": 70.0, "short_to_second_pass": 7, "short_translated_final": 68,
         "short_rolled_back_first_pass": 10,
         "long_strings": 30, "long_seconds": 90.0, "long_to_second_pass": 0, "long_translated_final": 27,
         "long_rolled_back_first_pass": 3,
         "to_second_pass": 7, "seconds_second_pass": 14.0, "model_errors": {}, "parallel_parts": False}
    COUNTS = {"pack": "ATM10 8.4", "how": "тест", "short": 1000, "long": 100}

    def test_from_measured_rates_and_shares(self):
        text = "\n".join(speed_test.pack_estimate(dict(self.R), self.COUNTS))
        # 1000 × 1 с + 100 × 3 с + 1000 × 7/70 × 2 с = 1500 с
        self.assertIn("короткие: 1.00 с на строку → 17 мин; длинные: 3.00 с → 5 мин", text)
        self.assertIn("откат в первом проходе: 14% коротких, 10% длинных; во второй проход около 100 строк по 2.00 с → 3 мин", text)
        self.assertIn("всего около 25 мин; без перевода останется около 39 строк", text)

    def test_parallel_takes_the_longer_part(self):
        text = "\n".join(speed_test.pack_estimate(dict(self.R, parallel_parts=True), self.COUNTS))
        self.assertIn("всего около 20 мин", text)  # max(1000, 300) + 200 с
        self.assertIn("(идут одновременно)", text)

    def test_atm10_counts_ship_with_the_strings(self):
        counts = speed_test.pack_counts(None)
        self.assertEqual((counts["pack"], counts["short"], counts["long"]), ("ATM10 8.4", 65362, 3413))
        self.assertEqual(len(speed_test.bundled_strings()), 100)

    def test_counts_of_a_given_pack(self):
        base = tempfile.mkdtemp()
        os.makedirs(os.path.join(base, "mods"))
        en = {"a": "Iron Ingot", "b": "Gold Ingot", "c": LONG, "d": "minecraft:stone"}
        with zipfile.ZipFile(os.path.join(base, "mods", "a.jar"), "w") as z:
            z.writestr("assets/a/lang/en_us.json", json.dumps(en))
        counts = speed_test.pack_counts(base)
        self.assertEqual((counts["short"], counts["long"]), (2, 1))


class RejectionReason(unittest.TestCase):
    def setUp(self):
        self.tr = translator(Model("m", lambda t: t), None)

    def reason(self, out, source):
        from src.translators import _extract_tokens
        return self.tr._rejection_reason(out, _extract_tokens(source), "ru_ru", source)

    def test_each_check_has_a_name(self):
        self.assertEqual(self.reason(BAD, "Minecraft"), "mixed_script")
        self.assertEqual(self.reason("鉄のインゴット", "Iron Ingot"), "cjk")
        self.assertEqual(self.reason("Железный слиток %s", "Iron Ingot"), "tokens")
        self.assertEqual(self.reason("Железный слиток", "Iron Ingot"), None)
        self.assertEqual(self.reason("", "Iron Ingot"), "empty")
        self.tr._last_error = LLMClientError("x")
        self.assertEqual(self.reason(None, "Iron Ingot"), "error")
        for key in ("mixed_script", "cjk", "tokens", "empty", "error", "leftover_english", "too_long", "glossary"):
            self.assertIn(key, speed_test.REASON_TITLES)

    def test_not_recorded_in_a_normal_run(self):
        self.assertIsNone(self.tr.outcomes)
        self.assertTrue(self.tr._answer_rejected(BAD, (), "ru_ru", "Minecraft"))
        self.assertIsNone(self.tr.outcomes)


if __name__ == "__main__":
    unittest.main()
