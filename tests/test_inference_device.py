"""Подпись в интерфейсе: «GPU: ...» только если сборка llama_cpp умеет считать на GPU."""
from __future__ import annotations

import unittest
from unittest import mock

from src.llm import hardware_probe as hp


def info(name, available=True):
    return hp.HardwareInfo(available, name, 8188 if available else 0, 32000, 14, "standard", 0.0)


class Device(unittest.TestCase):
    def test_cuda_build_with_nvidia(self):
        self.assertEqual(hp.inference_device(info("NVIDIA GeForce RTX 4060 Ti"), True), "GPU: NVIDIA GeForce RTX 4060 Ti")

    def test_cpu_build_with_nvidia_says_so(self):
        self.assertEqual(
            hp.inference_device(info("NVIDIA GeForce RTX 4060 Ti"), False),
            "CPU (есть NVIDIA GeForce RTX 4060 Ti, но сборка без CUDA)",
        )

    def test_no_gpu(self):
        self.assertEqual(hp.inference_device(info("", False), True), "CPU")
        self.assertEqual(hp.inference_device(None, False), "CPU")

    def test_apple_metal(self):
        self.assertEqual(hp.inference_device(info("Apple Silicon (Metal, unified memory)"), True), "GPU: Apple Silicon (Metal, unified memory)")

    def test_without_llama_cpp(self):
        with mock.patch.object(hp, "gpu_offload_supported", return_value=None):
            self.assertEqual(hp.inference_device(info("NVIDIA GeForce RTX 4060 Ti")), "локальная модель недоступна")

    def test_real_build_on_this_machine_is_asked_once(self):
        hp._OFFLOAD.clear()
        first = hp.gpu_offload_supported()
        self.assertIn(first, (True, False, None))
        self.assertIs(hp.gpu_offload_supported(), first)
        self.assertEqual(len(hp._OFFLOAD), 1)

    def test_api_sends_the_device_to_the_interface(self):
        from src.gui.api import Api
        with mock.patch.object(hp, "gpu_offload_supported", return_value=False):
            data = Api._hw_dict(Api.__new__(Api), info("NVIDIA GeForce RTX 4060 Ti"))
        self.assertEqual(data["device"], "CPU (есть NVIDIA GeForce RTX 4060 Ti, но сборка без CUDA)")


if __name__ == "__main__":
    unittest.main()
