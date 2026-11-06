"""
Почему на ПК «llama_cpp умеет GPU: None»: настоящий текст ошибки, пути поиска DLL,
разбор таблицы импорта, проверка сборки в CI и отчёт --diagnose.
"""
from __future__ import annotations

import importlib.abc
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

from src import win_dll
from src.llm import hardware_probe as hp
from tools import check_win_bundle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_pe(imports=(), delay=(), old_delay=False, pe32=False) -> bytes:
    """Минимальный PE: одна секция с таблицей импорта, таблицей отложенного импорта и именами."""
    image_base = 0x10000000 if pe32 else 0x180000000
    va, raw = 0x1000, 0x200
    imp_size = 20 * (len(imports) + 1) if imports else 0
    delay_off = imp_size
    delay_size = 32 * (len(delay) + 1) if delay else 0
    str_off = delay_off + delay_size
    strings, name_rvas = b"", []
    for n in list(imports) + list(delay):
        name_rvas.append(va + str_off + len(strings))
        strings += n.encode() + b"\0"
    body = bytearray()
    for i, _ in enumerate(imports):
        body += struct.pack("<IIIII", 0, 0, 0, name_rvas[i], 1)
    if imports:
        body += b"\0" * 20
    for j, _ in enumerate(delay):
        rva = name_rvas[len(imports) + j]
        body += struct.pack("<8I", 0 if old_delay else 1, rva + (image_base if old_delay else 0), 0, 0, 0, 0, 0, 0)
    if delay:
        body += b"\0" * 32
    body += strings

    dirs_off, n_off = (96, 92) if pe32 else (112, 108)
    opt = bytearray(dirs_off + 16 * 8)
    struct.pack_into("<H", opt, 0, 0x10B if pe32 else 0x20B)
    if pe32:
        struct.pack_into("<I", opt, 28, image_base)
    else:
        struct.pack_into("<Q", opt, 24, image_base)
    struct.pack_into("<I", opt, n_off, 16)
    if imports:
        struct.pack_into("<II", opt, dirs_off + 8, va, imp_size)
    if delay:
        struct.pack_into("<II", opt, dirs_off + 8 * 13, va + delay_off, delay_size)
    head = bytearray(b"MZ" + b"\0" * 0x3A + struct.pack("<I", 0x40))
    head += b"PE\0\0" + struct.pack("<HHIIIHH", 0x14C if pe32 else 0x8664, 1, 0, 0, 0, len(opt), 0x2022)
    head += opt
    head += b".idata\0\0" + struct.pack("<IIII", len(body), va, len(body), raw) + b"\0" * 16
    head += b"\0" * (raw - len(head))
    return bytes(head + body)


def write_lib(folder, spec):
    os.makedirs(folder, exist_ok=True)
    for name, (imports, delay) in spec.items():
        with open(os.path.join(folder, name), "wb") as f:
            f.write(make_pe(imports, delay))


# Набор как в колесе CUDA: ggml-cuda тянет CUDA runtime и драйвер.
CUDA_LIB = {
    "llama.dll": (["ggml.dll", "ggml-base.dll", "KERNEL32.dll", "MSVCP140.dll", "VCRUNTIME140.dll",
                   "VCRUNTIME140_1.dll", "api-ms-win-crt-runtime-l1-1-0.dll"], []),
    "ggml.dll": (["ggml-base.dll", "KERNEL32.dll"], []),
    "ggml-base.dll": (["KERNEL32.dll", "VCRUNTIME140.dll"], []),
    "ggml-cpu.dll": (["ggml-base.dll", "KERNEL32.dll"], []),
    "ggml-cuda.dll": (["ggml-base.dll", "cudart64_12.dll", "cublas64_12.dll", "nvcuda.dll", "KERNEL32.dll"], []),
    "mtmd.dll": (["llama.dll", "ggml.dll"], []),
}


class PeImports(unittest.TestCase):
    def parse(self, data):
        with tempfile.NamedTemporaryFile(suffix=".dll", delete=False) as f:
            f.write(data)
        try:
            return win_dll.pe_imports(f.name)
        finally:
            os.unlink(f.name)

    def test_pe32_plus_imports_and_delay(self):
        info = self.parse(make_pe(["KERNEL32.dll", "cudart64_12.dll"], ["nvcuda.dll"]))
        self.assertEqual(info, {"machine": "x64", "imports": ["KERNEL32.dll", "cudart64_12.dll"], "delay": ["nvcuda.dll"]})

    def test_pe32_and_old_delay_format_with_virtual_addresses(self):
        info = self.parse(make_pe(["KERNEL32.dll"], ["ole32.dll"], old_delay=True, pe32=True))
        self.assertEqual(info, {"machine": "x86", "imports": ["KERNEL32.dll"], "delay": ["ole32.dll"]})

    def test_no_imports(self):
        self.assertEqual(self.parse(make_pe())["imports"], [])

    def test_not_a_pe(self):
        with self.assertRaises(win_dll.PEError):
            self.parse(b"\x7fELF" + b"\0" * 100)

    def test_real_dll_from_pywebview_matches_objdump(self):
        # objdump -p: «DLL Name: KERNEL32.dll»; ADVAPI32.dll и ole32.dll видны в строках файла.
        import webview
        path = os.path.join(os.path.dirname(webview.__file__), "lib", "runtimes", "win-x64", "native", "WebView2Loader.dll")
        if not os.path.exists(path):
            self.skipTest("нет WebView2Loader.dll")
        info = win_dll.pe_imports(path)
        self.assertEqual(info["machine"], "x64")
        self.assertEqual(info["imports"], ["KERNEL32.dll"])
        self.assertEqual(sorted(x.lower() for x in info["delay"]), ["advapi32.dll", "ole32.dll"])


class LibFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lib = os.path.join(self.tmp, "lib")
        write_lib(self.lib, CUDA_LIB)

    def test_dependencies_load_first(self):
        order = win_dll.dependency_order(self.lib)
        self.assertEqual(sorted(order), sorted(CUDA_LIB))
        for name, (imports, _) in CUDA_LIB.items():
            for dep in imports:
                if dep in CUDA_LIB:
                    self.assertLess(order.index(dep), order.index(name), f"{dep} раньше {name}")
        self.assertEqual(order[0], "ggml-base.dll")

    def test_missing_cuda_runtime_and_vc_are_named(self):
        system = os.path.join(self.tmp, "System32")
        write_lib(system, {"KERNEL32.dll": ([], []), "nvcuda.dll": ([], [])})
        missing = win_dll.unresolved_imports(self.lib, [system])
        self.assertEqual(missing["ggml-cuda.dll"], ["cudart64_12.dll", "cublas64_12.dll"])
        self.assertEqual(missing["llama.dll"], ["MSVCP140.dll", "VCRUNTIME140.dll", "VCRUNTIME140_1.dll"])
        self.assertNotIn("ggml.dll", missing)  # api-ms-win-* и соседние DLL не в счёт

    def test_find_ignores_case(self):
        self.assertEqual(win_dll.find_in("GGML-CUDA.DLL", [self.lib]), os.path.join(self.lib, "ggml-cuda.dll"))
        self.assertIsNone(win_dll.find_in("cudart64_12.dll", [self.lib, os.path.join(self.tmp, "нет")]))


class SearchPath(unittest.TestCase):
    def setUp(self):
        win_dll._DLL_HANDLES.clear()
        self.addCleanup(win_dll._DLL_HANDLES.clear)
        self.tmp = tempfile.mkdtemp()
        self.meipass = os.path.join(self.tmp, "_internal")
        self.lib = os.path.join(self.meipass, "llama_cpp", "lib")
        self.cuda = os.path.join(self.tmp, "CUDA", "v12.4")
        for d in (self.lib, os.path.join(self.cuda, "bin")):
            os.makedirs(d)

    def run_prepare(self, env):
        added_dirs = []
        with mock.patch.object(sys, "platform", "win32"), \
             mock.patch.object(sys, "_MEIPASS", self.meipass, create=True), \
             mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "executable", os.path.join(self.tmp, "MinecraftTranslator.exe")), \
             mock.patch.object(win_dll, "llama_cpp_dir", return_value=os.path.join(self.meipass, "llama_cpp")), \
             mock.patch.object(os, "add_dll_directory", side_effect=lambda d: added_dirs.append(d) or d, create=True), \
             mock.patch.dict(os.environ, env, clear=True):
            added = win_dll.prepare_search_path()
            again = win_dll.prepare_search_path()
            path = os.environ["PATH"]
        return added, again, path, added_dirs

    def test_lib_bundle_exe_and_cuda_go_first_once(self):
        added, again, path, dll_dirs = self.run_prepare({"PATH": "/system32", "CUDA_PATH": self.cuda})
        expected = [self.lib, self.meipass, self.tmp, os.path.join(self.cuda, "bin")]
        self.assertEqual(added, expected)
        self.assertEqual(path.split(os.pathsep), expected + ["/system32"])
        self.assertEqual(dll_dirs, expected)
        self.assertEqual(again, [])  # второй вызов ничего не добавляет

    def test_bundled_cuda_libraries_before_the_toolkit(self):
        bins = [os.path.join(self.meipass, "nvidia", p, "bin") for p in ("cublas", "cuda_runtime")]
        for d in bins:
            os.makedirs(d)
        added, _, _, dll_dirs = self.run_prepare({"PATH": "/system32", "CUDA_PATH": self.cuda})
        self.assertEqual(added, [self.lib, self.meipass, self.tmp] + bins + [os.path.join(self.cuda, "bin")])
        self.assertEqual(dll_dirs, added)

    def test_nothing_on_mac(self):
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual(win_dll.prepare_search_path(), [])


class BundleCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.source = os.path.join(self.tmp, "site", "llama_cpp", "lib")
        write_lib(self.source, CUDA_LIB)
        self.dist = os.path.join(self.tmp, "dist", "MinecraftTranslator")
        self.internal = os.path.join(self.dist, "_internal")
        write_lib(os.path.join(self.internal, "llama_cpp", "lib"), CUDA_LIB)
        write_lib(self.internal, {n: ([], []) for n in win_dll.VC_RUNTIME})

    def test_complete_bundle_passes_and_shows_cuda_gaps(self):
        errors, report = check_win_bundle.check(self.dist, self.source, system=[])
        self.assertEqual(errors, [])
        text = "\n".join(report)
        self.assertIn("ggml-cuda.dll [x64]", text)
        self.assertIn("не найдены: cudart64_12.dll, cublas64_12.dll, nvcuda.dll", text)

    def test_missing_lib_file_and_vc_runtime_fail(self):
        os.remove(os.path.join(self.internal, "llama_cpp", "lib", "ggml-cuda.dll"))
        os.remove(os.path.join(self.internal, "vcruntime140_1.dll"))
        errors, _ = check_win_bundle.check(self.dist, self.source, system=[])
        self.assertEqual(errors, ["нет в сборке: llama_cpp/lib/ggml-cuda.dll", "нет в сборке: vcruntime140_1.dll"])
        with mock.patch("builtins.print"):
            self.assertEqual(check_win_bundle.main(["x", self.dist, self.source]), 1)

    def test_variant_b_has_its_cuda_dlls_once(self):
        cuda = {"cuda_runtime": ["cudart64_12.dll"], "cublas": ["cublas64_12.dll", "cublasLt64_12.dll"]}
        for pkg, names in cuda.items():
            write_lib(os.path.join(self.internal, "nvidia", pkg, "bin"), {n: ([], []) for n in names})
        errors, report = check_win_bundle.check(self.dist, self.source, system=[], expect_cuda=True)
        self.assertEqual(errors, [])
        text = "\n".join(report)
        self.assertIn("cublasLt64_12.dll: ", text)
        self.assertIn("копий в сборке: 1", text)
        self.assertIn("не найдены: nvcuda.dll", text)  # на раннере нет драйвера, cudart и cublas нашлись
        os.remove(os.path.join(self.internal, "nvidia", "cublas", "bin", "cublasLt64_12.dll"))
        errors, _ = check_win_bundle.check(self.dist, self.source, system=[], expect_cuda=True)
        self.assertEqual(errors, ["нет в сборке: nvidia/cublas/bin/cublasLt64_12.dll"])

    def test_variant_a_does_not_require_cuda_dlls(self):
        with mock.patch("builtins.print"):
            self.assertEqual(check_win_bundle.main(["x", self.dist, self.source]), 0)
            self.assertEqual(check_win_bundle.main(["x", self.dist, self.source, "--expect-cuda"]), 1)

    def test_ci_runs_the_check_on_windows(self):
        with open(os.path.join(ROOT, ".github", "workflows", "build.yml"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("python -X utf8 tools/check_win_bundle.py dist/MinecraftTranslator ${{ matrix.bundle_cuda == '1' && '--expect-cuda' || '' }}", text)
        build = text.index("pyinstaller --noconfirm --clean")
        self.assertLess(build, text.index("tools/check_win_bundle.py"))
        self.assertLess(text.index("tools/check_win_bundle.py"), text.index("Compress-Archive"))


class _FailingImport(importlib.abc.MetaPathFinder):
    """Как на ПК: import llama_cpp падает при загрузке llama.dll."""

    def find_spec(self, name, path=None, target=None):
        if name == "llama_cpp" or name.startswith("llama_cpp."):
            raise FileNotFoundError(
                "Could not find module 'C:\\MT\\_internal\\llama_cpp\\lib\\llama.dll' (or one of its dependencies). "
                "Try using the full path with constructor syntax."
            )
        return None


def without_llama_cpp(test):
    """Убирает загруженную llama_cpp и подставляет падающий импорт."""
    saved = {k: v for k, v in sys.modules.items() if k == "llama_cpp" or k.startswith("llama_cpp.")}
    for k in saved:
        del sys.modules[k]
    finder = _FailingImport()
    sys.meta_path.insert(0, finder)
    offload, error = list(hp._OFFLOAD), list(hp._OFFLOAD_ERROR)
    hp._OFFLOAD.clear()
    hp._OFFLOAD_ERROR.clear()

    def restore():
        sys.meta_path.remove(finder)
        sys.modules.update(saved)
        hp._OFFLOAD[:] = offload
        hp._OFFLOAD_ERROR[:] = error

    test.addCleanup(restore)


class RealErrorText(unittest.TestCase):
    def setUp(self):
        without_llama_cpp(self)

    def test_probe_keeps_the_exception(self):
        self.assertIsNone(hp.gpu_offload_supported())
        self.assertIn("FileNotFoundError: Could not find module", hp.llama_cpp_error())
        self.assertIn("llama.dll", hp.llama_cpp_error())
        self.assertIn("Traceback", hp.llama_cpp_error(full=True))

    def test_interface_line_shows_it(self):
        device = hp.inference_device(hp.HardwareInfo(True, "NVIDIA GeForce RTX 4060 Ti", 8188, 32000, 14, "standard", 0.0))
        self.assertTrue(device.startswith("локальная модель недоступна (llama_cpp: FileNotFoundError: Could not find module"))

    def test_window_log_shows_it(self):
        from src.gui.api import Api
        api = Api.__new__(Api)
        api._lock, api._logs = __import__("threading").Lock(), []
        api._log_llama_cpp_error()
        self.assertIn("llama_cpp не загрузилась: FileNotFoundError: Could not find module", api._logs[0])
        self.assertIn("--diagnose", api._logs[1])

    def test_local_client_names_the_cause(self):
        from src.llm.local_llamacpp import LocalLlamaCppClient
        from src.llm.base import LLMClientError
        client = LocalLlamaCppClient(model_path="/нет/model.gguf")
        with self.assertRaises(LLMClientError) as ctx:
            client._ensure_llm()
        self.assertIn("Could not find module", str(ctx.exception))
        self.assertIn("--diagnose", str(ctx.exception))

    def test_diagnose_report_without_windows(self):
        tmp = tempfile.mkdtemp()
        write_lib(tmp, CUDA_LIB)
        with mock.patch.dict(os.environ, {"LLAMA_CPP_LIB_PATH": tmp}):
            text = win_dll.diagnose()
        for part in ("Файлы в папке библиотек:", "ggml-cuda.dll [x64]:", "cudart64_12.dll → НЕ НАЙДЕНА",
                     "ggml-base.dll: пропущено, не Windows", "vcruntime140_1.dll:", "nvcuda.dll (драйвер NVIDIA):",
                     "import llama_cpp:", "FileNotFoundError: Could not find module"):
            self.assertIn(part, text)
        loads = text[text.index("Загрузка по очереди"):]
        self.assertLess(loads.index("ggml-base.dll"), loads.index("llama.dll"))
        self.assertIn("Первая DLL, которая не загрузилась: нет", text)

    def test_diagnose_names_the_first_dll_that_fails_with_its_code(self):
        tmp = tempfile.mkdtemp()
        write_lib(tmp, CUDA_LIB)
        write_lib(tmp, {"cudart64_12.dll": ([], [])})  # как в сборке B: нашлась рядом

        def fake_load(path):
            name = os.path.basename(path)
            if name == "ggml-cuda.dll":
                return f"ОШИБКА WinError 126: {win_dll.WIN_ERRORS[126]} (Could not find module)"
            return "OK"

        with mock.patch.dict(os.environ, {"LLAMA_CPP_LIB_PATH": tmp}), \
             mock.patch.object(sys, "platform", "win32"), \
             mock.patch.object(win_dll, "_load", side_effect=fake_load), \
             mock.patch.object(win_dll, "prepare_search_path", return_value=[]), \
             mock.patch.object(win_dll, "_nvcuda_lines", return_value=["nvcuda.dll: проверка подменена"]):
            text = win_dll.diagnose()
        loads = text[text.index("Загрузка по очереди"):]
        self.assertLess(loads.index("cudart64_12.dll: OK"), loads.index("ggml-base.dll: OK"))
        self.assertIn("Первая DLL, которая не загрузилась: ggml-cuda.dll: ОШИБКА WinError 126", text)


class DiagnoseFlag(unittest.TestCase):
    def test_main_writes_the_report_and_does_not_open_the_window(self):
        from src import config, main
        tmp = tempfile.mkdtemp()
        with mock.patch.object(config, "APP_DATA_DIR", tmp), \
             mock.patch.object(sys, "argv", ["MinecraftTranslator.exe", "--diagnose"]), \
             mock.patch.object(win_dll, "diagnose", return_value="отчёт"), \
             mock.patch("builtins.print"), \
             mock.patch.dict(sys.modules, {"webview": None}):
            main.main()
        with open(os.path.join(tmp, "diagnose.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "отчёт")


if __name__ == "__main__":
    unittest.main()
