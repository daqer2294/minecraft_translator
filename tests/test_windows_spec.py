"""Спецификация Windows-сборки: версия в свойствах exe, без UPX."""
from __future__ import annotations

import ast
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Spec(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "MinecraftTranslator-win.spec"), encoding="utf-8") as f:
            self.src = f.read()
        ast.parse(self.src)

    def test_spec_reads_the_app_version(self):
        from src.version import __version__
        self.assertIn('os.path.join("src", "version.py")', self.src)
        self.assertIn('StringStruct("OriginalFilename", "MinecraftTranslator.exe")', self.src)
        self.assertIn("version=version_info", self.src)
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    # Модуль версий PyInstaller требует pefile: он есть на Windows, на Mac его нет.
    @unittest.skipUnless(importlib.util.find_spec("pefile"), "pefile ставится только на Windows")
    def test_version_resource_from_the_app_version(self):
        ns = {}
        os.chdir(ROOT)
        exec(self.src.split("block_cipher = None")[0], ns)
        from src.version import __version__
        self.assertEqual(ns["VERSION"], __version__)
        text = str(ns["version_info"])
        self.assertIn("MinecraftTranslator.exe", text)
        self.assertIn(__version__, text)

    def test_folder_build_not_onefile(self):
        tree = ast.parse(self.src)
        calls = {n.func.id: n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertIn("COLLECT", calls)
        exe = calls["EXE"]
        self.assertTrue(any(k.arg == "exclude_binaries" and k.value.value is True for k in exe.keywords))
        self.assertNotIn("a.binaries", [ast.unparse(a) for a in exe.args])

    def test_ci_packs_the_folder(self):
        with open(os.path.join(ROOT, ".github", "workflows", "build.yml"), encoding="utf-8") as f:
            ci = f.read()
        self.assertIn("Compress-Archive -Path dist/MinecraftTranslator -DestinationPath", ci)
        self.assertNotIn("Copy-Item dist/MinecraftTranslator.exe", ci)

    def test_two_cuda_variants_in_one_run(self):
        import yaml
        with open(os.path.join(ROOT, ".github", "workflows", "build.yml"), encoding="utf-8") as f:
            ci = yaml.safe_load(f)
        job = ci["jobs"]["build"]
        legs = {(x["platform"], x["variant"]): x for x in job["strategy"]["matrix"]["include"]}
        a, b = legs[("win", "-cuda-a")], legs[("win", "-cuda-b")]
        self.assertNotIn(("win", "-cuda"), legs)
        self.assertTrue(a["optional"] and b["optional"])
        self.assertEqual(a["reqfile"], "requirements-win-cuda.txt")
        self.assertNotIn("bundle_cuda", a)
        self.assertEqual(b["reqfile"], "requirements-win-cuda-b.txt")
        self.assertEqual(b["bundle_cuda"], "1")
        self.assertEqual(legs[("win", "")]["reqfile"], "requirements-win.txt")
        # Имя артефакта: MinecraftTranslator-win-x64-cuda-a и MinecraftTranslator-win-x64-cuda-b.
        steps = {st["name"]: st for st in job["steps"]}
        self.assertEqual(steps["Upload artifacts"]["with"]["name"],
                         "MinecraftTranslator-${{ matrix.platform }}-${{ matrix.arch }}${{ matrix.variant }}")
        self.assertEqual(steps["(CUDA) inspect llama_cpp libraries"]["if"], "startsWith(matrix.variant, '-cuda')")
        self.assertEqual(steps["(CUDA) diagnose on the runner"]["if"], "startsWith(matrix.variant, '-cuda')")
        self.assertIn("!startsWith(matrix.variant, '-cuda')", steps["Verify local inference import (fail job if broken)"]["if"])
        self.assertEqual(steps["Build with PyInstaller"]["env"]["MCT_BUNDLE_CUDA"], "${{ matrix.bundle_cuda || '' }}")
        self.assertIn("--expect-cuda", steps["(Windows) check DLLs in the built folder"]["run"])
        with open(os.path.join(ROOT, "requirements-win-cuda.txt"), encoding="utf-8") as f:
            req = f.read()
        self.assertIn("/whl/cu124", req)
        self.assertIn("llama-cpp-python==0.3.30", req)
        with open(os.path.join(ROOT, "requirements-win-cuda-b.txt"), encoding="utf-8") as f:
            req_b = [x for x in f.read().splitlines() if x and not x.startswith("#")]
        # CUDA 12.4 Update 1: cudart 12.4.127, cuBLAS 12.4.5.8 (release notes NVIDIA).
        self.assertEqual(req_b, ["-r requirements-win-cuda.txt", "nvidia-cuda-runtime-cu12==12.4.127",
                                 "nvidia-cublas-cu12==12.4.5.8"])

    def _cuda_block(self, site, env):
        import sys
        from unittest import mock
        block = self.src[self.src.index("CUDA_DLLS = "):self.src.index("a = Analysis(")]
        ns = {"os": os, "binaries": []}
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "path", [site]):
            exec(block, ns)
        return ns

    def _site(self, names):
        import tempfile
        site = tempfile.mkdtemp()
        for rel in names:
            os.makedirs(os.path.dirname(os.path.join(site, rel)), exist_ok=True)
            open(os.path.join(site, rel), "wb").close()
        return site

    def test_variant_b_bundles_the_cuda_dlls(self):
        site = self._site(["nvidia/cuda_runtime/bin/cudart64_12.dll", "nvidia/cublas/bin/cublas64_12.dll",
                           "nvidia/cublas/bin/cublasLt64_12.dll", "nvidia/cublas/bin/nvblas64_12.dll"])
        ns = self._cuda_block(site, {"MCT_BUNDLE_CUDA": "1"})
        self.assertEqual(ns["binaries"], [
            (os.path.join(site, "nvidia", "cuda_runtime", "bin", "cudart64_12.dll"), "nvidia/cuda_runtime/bin"),
            (os.path.join(site, "nvidia", "cublas", "bin", "cublas64_12.dll"), "nvidia/cublas/bin"),
            (os.path.join(site, "nvidia", "cublas", "bin", "cublasLt64_12.dll"), "nvidia/cublas/bin"),
        ])
        from tools import check_win_bundle
        self.assertEqual(ns["CUDA_DLLS"], check_win_bundle.CUDA_DLLS)

    def test_variant_b_fails_without_the_packages(self):
        site = self._site(["nvidia/cuda_runtime/bin/cudart64_12.dll"])
        with self.assertRaises(SystemExit) as ctx:
            self._cuda_block(site, {"MCT_BUNDLE_CUDA": "1"})
        self.assertIn("cublas64_12.dll", str(ctx.exception))

    def test_variant_a_unchanged(self):
        site = self._site(["nvidia/cuda_runtime/bin/cudart64_12.dll"])
        self.assertEqual(self._cuda_block(site, {"MCT_BUNDLE_CUDA": ""})["binaries"], [])

    def test_upx_is_off(self):
        self.assertIn("upx=False", self.src)
        self.assertNotIn("upx=True", self.src)


if __name__ == "__main__":
    unittest.main()
