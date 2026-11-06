# tools/check_win_bundle.py
"""
Проверка готовой Windows-сборки (папка dist/MinecraftTranslator), шаг CI.

Падает (код 1), если:
  * в _internal/llama_cpp/lib нет какого-то файла из llama_cpp/lib установленного пакета;
  * в сборке нет vcruntime140.dll, vcruntime140_1.dll или msvcp140.dll.
Дополнительно печатает, что импортирует каждая DLL llama_cpp и что из этого не нашлось
ни в сборке, ни в System32 раннера. На раннере нет драйвера NVIDIA, поэтому nvcuda.dll
там не найдётся всегда; cudart/cublas видно, пришли ли они с колесом.

Запуск: python tools/check_win_bundle.py dist/MinecraftTranslator [папка llama_cpp/lib пакета]
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import win_dll  # noqa: E402


def check(dist: str, source_lib: str | None, system: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Возвращает (ошибки, строки отчёта)."""
    errors, report = [], []
    internal = os.path.join(dist, "_internal")
    if not os.path.isdir(internal):
        internal = dist
    bundle_lib = os.path.join(internal, "llama_cpp", "lib")

    if source_lib:
        expected = sorted(os.listdir(source_lib))
        have = {f.lower() for f in os.listdir(bundle_lib)} if os.path.isdir(bundle_lib) else set()
        missing = [f for f in expected if f.lower() not in have]
        report.append(f"llama_cpp/lib пакета: {len(expected)} файлов, в сборке: {len(have)}")
        for f in expected:
            report.append(f"  {f}: {'есть' if f.lower() in have else 'НЕТ'}")
        errors += [f"нет в сборке: llama_cpp/lib/{f}" for f in missing]

    bundle_dirs = [dist, internal, bundle_lib]
    for name in win_dll.VC_RUNTIME:
        where = win_dll.find_in(name, bundle_dirs)
        report.append(f"{name}: {where or 'НЕТ'}")
        if not where:
            errors.append(f"нет в сборке: {name}")

    if os.path.isdir(bundle_lib):
        search = bundle_dirs + (system if system is not None else win_dll.system_dirs())
        report.append("Что импортируют DLL llama_cpp и чего нет ни в сборке, ни в System32:")
        for f in win_dll.dependency_order(bundle_lib):
            try:
                info = win_dll.pe_imports(os.path.join(bundle_lib, f))
            except Exception as e:
                report.append(f"  {f}: не разобрать ({e})")
                continue
            deps = list(info["imports"]) + [d + " (отложенно)" for d in info["delay"]]
            shown = [d for d in deps if not win_dll.is_api_set(d.split(" ")[0])]
            missing = [d for d in shown if win_dll.find_in(d.split(" ")[0], search) is None]
            report.append(f"  {f} [{info['machine']}]: {', '.join(shown)}")
            if missing:
                report.append(f"    не найдены: {', '.join(missing)}")
    return errors, report


def main(argv: list[str]) -> int:
    try:  # консоль раннера Windows в cp1252 не печатает кириллицу
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    dist = argv[1] if len(argv) > 1 else os.path.join("dist", "MinecraftTranslator")
    source_lib = argv[2] if len(argv) > 2 else win_dll.llama_cpp_lib_dir()
    if source_lib and not os.path.isdir(source_lib):
        source_lib = None
    errors, report = check(dist, source_lib)
    print("\n".join(report))
    for e in errors:
        print(f"::error::{e}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
