"""Ollama ищется по 127.0.0.1: на Windows «localhost» сначала идёт в ::1, а Ollama слушает IPv4."""
from __future__ import annotations

import unittest

from src import config
from src.llm import ollama_probe


class Address(unittest.TestCase):
    def test_defaults_use_ipv4(self):
        self.assertEqual(ollama_probe.OLLAMA_DEFAULT_URL, "http://127.0.0.1:11434")
        self.assertEqual(config.ProviderConfig().ollama_base_url, "http://127.0.0.1:11434")
        self.assertNotIn("localhost", config.OLLAMA_BASE_URL)

    def test_no_localhost_left_in_defaults(self):
        import inspect
        for module in (ollama_probe, config):
            for line in inspect.getsource(module).splitlines():
                if "11434" in line and not line.lstrip().startswith("#"):
                    self.assertNotIn("localhost", line, line)


if __name__ == "__main__":
    unittest.main()
