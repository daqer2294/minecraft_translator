"""nvidia-smi на Windows: без мигающего окна консоли, с поиском в старой папке NVSMI."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest import mock

from src.llm import hardware_probe as hp


class Result:
    def __init__(self, code, out):
        self.returncode, self.stdout = code, out


class NvidiaSmi(unittest.TestCase):
    def test_windows_hides_the_console_window(self):
        calls = []
        with mock.patch.object(hp.platform, "system", return_value="Windows"), \
                mock.patch.object(hp.subprocess, "run", side_effect=lambda *a, **k: calls.append(k) or Result(0, "NVIDIA GeForce RTX 4060 Ti, 8188\n")):
            self.assertEqual(hp._detect_nvidia(), ("NVIDIA GeForce RTX 4060 Ti", 8188))
        self.assertEqual(calls[0]["creationflags"], 0x08000000)
        self.assertIs(calls[0]["stdin"], subprocess.DEVNULL)

    def test_other_systems_have_no_creation_flags(self):
        calls = []
        with mock.patch.object(hp.platform, "system", return_value="Darwin"), \
                mock.patch.object(hp.subprocess, "run", side_effect=lambda *a, **k: calls.append(k) or Result(9, "")):
            self.assertIsNone(hp._detect_nvidia())
        self.assertNotIn("creationflags", calls[0])
        self.assertIs(calls[0]["stdin"], subprocess.DEVNULL)

    def test_old_driver_folder_is_tried_after_path(self):
        root = tempfile.mkdtemp()
        nvsmi = os.path.join(root, "NVIDIA Corporation", "NVSMI")
        os.makedirs(nvsmi)
        open(os.path.join(nvsmi, "nvidia-smi.exe"), "w").close()
        tried = []

        def run(cmd, **kwargs):
            tried.append(cmd[0])
            if cmd[0] == "nvidia-smi":
                raise FileNotFoundError()
            return Result(0, "NVIDIA GeForce GTX 1060, 6144\n")

        with mock.patch.object(hp.platform, "system", return_value="Windows"), \
                mock.patch.dict(os.environ, {"ProgramFiles": root, "SystemRoot": tempfile.mkdtemp()}), \
                mock.patch.object(hp.shutil, "which", return_value=None), \
                mock.patch.object(hp.subprocess, "run", side_effect=run):
            self.assertEqual(hp._detect_nvidia(), ("NVIDIA GeForce GTX 1060", 6144))
        self.assertEqual(tried, ["nvidia-smi", os.path.join(nvsmi, "nvidia-smi.exe")])

    def test_no_nvidia_anywhere(self):
        with mock.patch.object(hp.platform, "system", return_value="Windows"), \
                mock.patch.dict(os.environ, {"ProgramFiles": tempfile.mkdtemp(), "SystemRoot": tempfile.mkdtemp()}), \
                mock.patch.object(hp.shutil, "which", return_value=None), \
                mock.patch.object(hp.subprocess, "run", side_effect=FileNotFoundError()):
            self.assertIsNone(hp._detect_nvidia())


if __name__ == "__main__":
    unittest.main()
