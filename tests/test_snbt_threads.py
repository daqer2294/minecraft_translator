"""ftb_snbt_lib держит состояние лексера глобально: параллельный разбор ломал файлы."""
from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from src.processors.snbt_structured import translate_snbt_text_structured


class Echo:
    def translate(self, text, **kwargs):
        return text


def make_snbt(seed):
    lines = [f'quest.{seed:08X}{i:08X}.title: "Quest {seed} title number {i}"' for i in range(400)]
    return "{\n" + "\n".join(lines) + "\n}"


class ParallelParsing(unittest.TestCase):
    def test_six_threads_match_the_single_thread_result(self):
        texts = [make_snbt(i) for i in range(36)]
        expected = [translate_snbt_text_structured(t, Echo()) for t in texts]

        def work(text):
            return translate_snbt_text_structured(text, Echo())

        with ThreadPoolExecutor(6) as pool:
            actual = list(pool.map(work, texts))
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
