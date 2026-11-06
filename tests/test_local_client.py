"""In-process клиент: llama_cpp.Llama нельзя вызывать из нескольких потоков сразу."""
from __future__ import annotations

import sys
import threading
import time
import types
import unittest
from unittest import mock

from src.llm.base import LLMCancelledError
from src.llm.local_llamacpp import LocalLlamaCppClient


class FakeLlama:
    """Считает загрузки и одновременные вызовы вместо настоящего инференса."""

    loads = 0
    active = 0
    peak = 0
    guard = threading.Lock()
    delay = 0.02

    def __init__(self, **kwargs):
        time.sleep(0.02)
        with FakeLlama.guard:
            FakeLlama.loads += 1

    def create_chat_completion(self, **kwargs):
        with FakeLlama.guard:
            FakeLlama.active += 1
            FakeLlama.peak = max(FakeLlama.peak, FakeLlama.active)
        time.sleep(FakeLlama.delay)
        return self._reply()

    def _reply(self):
        with FakeLlama.guard:
            FakeLlama.active -= 1
        yield {"choices": [{"delta": {"content": "ответ"}, "finish_reason": "stop"}]}


class LocalClientConcurrency(unittest.TestCase):
    def setUp(self):
        FakeLlama.loads = FakeLlama.active = FakeLlama.peak = 0
        FakeLlama.delay = 0.02
        fake_module = types.ModuleType("llama_cpp")
        fake_module.Llama = FakeLlama
        patcher = mock.patch.dict(sys.modules, {"llama_cpp": fake_module})
        patcher.start()
        self.addCleanup(patcher.stop)
        exists = mock.patch("os.path.exists", return_value=True)
        exists.start()
        self.addCleanup(exists.stop)
        self.message = [{"role": "user", "content": "x"}]

    def _run_threads(self, clients, per_thread=3):
        results, errors = [], []

        def work(client):
            for _ in range(per_thread):
                try:
                    results.append(client.chat(self.message))
                except Exception as e:  # noqa: BLE001 - тест считает любую ошибку
                    errors.append(e)

        threads = [threading.Thread(target=work, args=(c,)) for c in clients]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results, errors

    def test_one_call_at_a_time_from_six_threads(self):
        client = LocalLlamaCppClient(model_path="m.gguf")
        results, errors = self._run_threads([client] * 6)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 18)
        self.assertEqual(FakeLlama.peak, 1)

    def test_model_is_loaded_once_when_threads_start_cold(self):
        client = LocalLlamaCppClient(model_path="m.gguf")
        self._run_threads([client] * 6, per_thread=1)
        self.assertEqual(FakeLlama.loads, 1)

    def test_two_clients_do_not_run_at_the_same_time(self):
        clients = [LocalLlamaCppClient(model_path="a.gguf"), LocalLlamaCppClient(model_path="b.gguf")]
        self._run_threads(clients)
        self.assertEqual(FakeLlama.peak, 1)

    def test_cancel_releases_threads_waiting_in_queue(self):
        FakeLlama.delay = 0.5
        client = LocalLlamaCppClient(model_path="m.gguf")
        cancel = threading.Event()
        outcome = {}

        def holder():
            client.chat(self.message)

        def waiter():
            started = time.time()
            try:
                client.chat(self.message, cancel_event=cancel)
                outcome["result"] = "ran"
            except LLMCancelledError:
                outcome["result"] = "cancelled"
            outcome["waited"] = time.time() - started

        first = threading.Thread(target=holder)
        first.start()
        time.sleep(0.1)
        second = threading.Thread(target=waiter)
        second.start()
        time.sleep(0.05)
        cancel.set()
        second.join()
        first.join()
        self.assertEqual(outcome["result"], "cancelled")
        self.assertLess(outcome["waited"], 0.35)

    def test_cancel_set_before_start_runs_nothing(self):
        client = LocalLlamaCppClient(model_path="m.gguf")
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(LLMCancelledError):
            client.chat(self.message, cancel_event=cancel)
        self.assertEqual(FakeLlama.peak, 0)

    def test_lock_is_released_after_inference_error(self):
        client = LocalLlamaCppClient(model_path="m.gguf")
        with mock.patch.object(FakeLlama, "create_chat_completion", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                client.chat(self.message)
        self.assertEqual(client.chat(self.message), "ответ")


if __name__ == "__main__":
    unittest.main()
