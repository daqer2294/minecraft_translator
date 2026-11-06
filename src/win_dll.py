# src/win_dll.py
"""
Библиотеки llama_cpp на Windows: где их искать, что им нужно и почему они не грузятся.

llama_cpp грузит свои DLL через ctypes с winmode=0. При этом Windows ищет зависимости
(ggml-cuda.dll → cudart64_12.dll, nvcuda.dll, vcruntime140.dll …) старым порядком:
папка exe, папка из SetDllDirectory, System32, папка Windows, PATH. Каталоги из
os.add_dll_directory в этом режиме не участвуют, поэтому prepare_search_path() кладёт
нужные папки и в PATH, и в add_dll_directory.

Разбор таблицы импорта PE написан здесь, чтобы не добавлять зависимость (pefile).
Модуль импортируется на любой ОС: разбор PE и отчёт проверяются тестами на Mac.
"""
from __future__ import annotations

import glob
import os
import struct
import subprocess
import sys
import traceback
from typing import Dict, List, Optional, Tuple

VC_RUNTIME = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")
CUDA_RUNTIME = ("cudart64_12.dll", "cublasLt64_12.dll", "cublas64_12.dll")
# Порядок загрузки, если зависимости в таблице импорта не позволяют выбрать его самим.
LOAD_ORDER_HINT = ("ggml-base", "ggml-cpu", "ggml-cuda", "ggml", "llama", "mtmd")
WIN_ERRORS = {
    126: "не найден сам файл или одна из его зависимостей",
    127: "в зависимости нет нужной функции (не та версия DLL)",
    193: "не та разрядность или файл повреждён",
    1114: "DLL упала при инициализации (DllMain)",
    5: "нет доступа",
}

_DLL_HANDLES: list = []  # os.add_dll_directory действует, пока жив возвращённый объект


# ---------------- разбор PE ----------------

class PEError(ValueError):
    pass


def pe_imports(path: str) -> Dict[str, object]:
    """
    Какие DLL импортирует файл: обычная таблица импорта и отложенная (delay-load).
    Возвращает {"machine": "x64" | "x86" | "arm64" | hex, "imports": [...], "delay": [...]}.
    """
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] != b"MZ" or len(data) < 0x40:
        raise PEError("нет заголовка MZ")
    (pe_off,) = struct.unpack_from("<I", data, 0x3C)
    if data[pe_off:pe_off + 4] != b"PE\0\0":
        raise PEError("нет подписи PE")
    machine, n_sections, _, _, _, opt_size, _ = struct.unpack_from("<HHIIIHH", data, pe_off + 4)
    opt = pe_off + 24
    (magic,) = struct.unpack_from("<H", data, opt)
    if magic == 0x20B:
        (image_base,) = struct.unpack_from("<Q", data, opt + 24)
        n_dirs_off, dirs_off = 108, 112
    elif magic == 0x10B:
        (image_base,) = struct.unpack_from("<I", data, opt + 28)
        n_dirs_off, dirs_off = 92, 96
    else:
        raise PEError(f"неизвестный Magic {magic:#x}")
    (n_dirs,) = struct.unpack_from("<I", data, opt + n_dirs_off)

    def directory(index: int) -> Tuple[int, int]:
        if index >= n_dirs:
            return 0, 0
        return struct.unpack_from("<II", data, opt + dirs_off + 8 * index)

    sections = []
    sec = opt + opt_size
    for i in range(n_sections):
        vsize, va, raw_size, raw_ptr = struct.unpack_from("<IIII", data, sec + 40 * i + 8)
        sections.append((va, max(vsize, raw_size), raw_ptr))

    def offset(rva: int) -> int:
        for va, size, raw in sections:
            if va <= rva < va + size:
                return rva - va + raw
        raise PEError(f"адрес {rva:#x} вне секций")

    def cstr(rva: int) -> str:
        start = offset(rva)
        end = data.index(b"\0", start)
        return data[start:end].decode("ascii", "replace")

    imports: List[str] = []
    rva, size = directory(1)
    if rva:
        pos = offset(rva)
        while pos + 20 <= len(data):
            oft, _, _, name_rva, first_thunk = struct.unpack_from("<IIIII", data, pos)
            if not (oft or name_rva or first_thunk):
                break
            imports.append(cstr(name_rva))
            pos += 20

    delay: List[str] = []
    rva, size = directory(13)
    if rva:
        pos = offset(rva)
        while pos + 32 <= len(data):
            attrs, name_rva = struct.unpack_from("<II", data, pos)
            if not name_rva:
                break
            if not attrs & 1:  # старый формат: в полях виртуальные адреса, а не RVA
                name_rva -= image_base
            delay.append(cstr(name_rva))
            pos += 32

    names = {0x8664: "x64", 0x14C: "x86", 0xAA64: "arm64"}
    return {"machine": names.get(machine, hex(machine)), "imports": imports, "delay": delay}


def is_api_set(name: str) -> bool:
    """api-ms-win-*.dll и ext-ms-*.dll Windows подставляет сама, файлов с такими именами нет."""
    low = name.lower()
    return low.startswith("api-ms-") or low.startswith("ext-ms-")


def find_in(name: str, dirs: List[str]) -> Optional[str]:
    """Первый файл с этим именем в списке папок, без учёта регистра."""
    low = name.lower()
    for d in dirs:
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for entry in entries:
            if entry.lower() == low:
                return os.path.join(d, entry)
    return None


# ---------------- где лежат DLL ----------------

def llama_cpp_dir() -> Optional[str]:
    """Папка пакета llama_cpp без его импорта (импорт как раз и падает)."""
    try:
        import importlib.util
        spec = importlib.util.find_spec("llama_cpp")
    except Exception:
        spec = None
    if spec is not None and spec.submodule_search_locations:
        for loc in spec.submodule_search_locations:
            if os.path.isdir(loc):
                return loc
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and os.path.isdir(os.path.join(meipass, "llama_cpp")):
        return os.path.join(meipass, "llama_cpp")
    return None


def llama_cpp_lib_dir() -> Optional[str]:
    override = os.environ.get("LLAMA_CPP_LIB_PATH")
    if override:
        return override
    pkg = llama_cpp_dir()
    return os.path.join(pkg, "lib") if pkg else None


def llama_cpp_version() -> str:
    for dist in ("llama_cpp_python", "llama-cpp-python"):
        try:
            from importlib.metadata import version
            return version(dist)
        except Exception:
            continue
    pkg = llama_cpp_dir()
    if pkg:
        try:
            import re
            with open(os.path.join(pkg, "__init__.py"), encoding="utf-8") as f:
                m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", f.read())
            if m:
                return m.group(1)
        except OSError:
            pass
    return "неизвестна"


def _cuda_bins() -> List[str]:
    """bin из CUDA_PATH и CUDA_PATH_V12_4 и т.п. В CUDA 13 библиотеки лежат в bin\\x64."""
    roots = [v for k, v in sorted(os.environ.items()) if k.upper() == "CUDA_PATH" or k.upper().startswith("CUDA_PATH_V")]
    out = []
    for root in roots:
        for sub in ("bin", os.path.join("bin", "x64")):
            out.append(os.path.join(root, sub))
    return out


def _nvidia_wheel_bins() -> List[str]:
    """bin пакетов nvidia-*-cu12 из pip, если они попали в сборку или в окружение."""
    bases = [getattr(sys, "_MEIPASS", "")] + [p for p in sys.path if p.endswith("site-packages")]
    out = []
    for base in bases:
        if base:
            out += sorted(glob.glob(os.path.join(base, "nvidia", "*", "bin")))
    return out


def candidate_dirs() -> List[str]:
    """Папки, где могут лежать DLL для llama_cpp, в порядке приоритета."""
    dirs = []
    lib = llama_cpp_lib_dir()
    if lib:
        dirs.append(lib)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(meipass)
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    # Свои CUDA-библиотеки из сборки раньше CUDA Toolkit: у того может быть другая версия 12.x.
    dirs += _nvidia_wheel_bins() + _cuda_bins()
    seen, out = set(), []
    for d in dirs:
        key = os.path.normcase(os.path.abspath(d))
        if key not in seen and os.path.isdir(d):
            seen.add(key)
            out.append(d)
    return out


def prepare_search_path() -> List[str]:
    """
    До импорта llama_cpp: папки с DLL в начало PATH и в add_dll_directory.
    Только на Windows. Возвращает добавленные в PATH папки.
    """
    if sys.platform != "win32" or _DLL_HANDLES:
        return []
    dirs = candidate_dirs()
    path = os.environ.get("PATH", "")
    present = {os.path.normcase(p.rstrip("\\/")) for p in path.split(os.pathsep) if p}
    added = [d for d in dirs if os.path.normcase(d.rstrip("\\/")) not in present]
    if added:
        os.environ["PATH"] = os.pathsep.join(added + ([path] if path else []))
    for d in dirs:
        try:
            _DLL_HANDLES.append(os.add_dll_directory(d))
        except (OSError, AttributeError):
            pass
    _DLL_HANDLES.append(None)  # второй вызов ничего не добавляет
    return added


def system_dirs() -> List[str]:
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    return [os.path.join(root, "System32"), root]


def loader_search_dirs() -> List[str]:
    """Порядок поиска зависимостей при winmode=0: папка exe, SetDllDirectory, System32, Windows, PATH."""
    dirs = [os.path.dirname(os.path.abspath(sys.executable))]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(meipass)
    dirs += system_dirs()
    dirs += [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    return dirs


# ---------------- проверка набора файлов ----------------

def dependency_order(lib_dir: str) -> List[str]:
    """DLL из папки так, чтобы каждая шла после своих зависимостей из этой же папки."""
    files = sorted(f for f in os.listdir(lib_dir) if f.lower().endswith(".dll"))
    by_low = {f.lower(): f for f in files}
    deps: Dict[str, List[str]] = {}
    for f in files:
        try:
            info = pe_imports(os.path.join(lib_dir, f))
            names = list(info["imports"]) + list(info["delay"])
        except (OSError, PEError, struct.error, ValueError):
            names = []
        deps[f] = [by_low[n.lower()] for n in names if n.lower() in by_low and n.lower() != f.lower()]

    def hint(f: str) -> int:
        stem = f.lower()[:-4]
        for i, h in enumerate(LOAD_ORDER_HINT):
            if stem == h or stem.startswith(h + "-") or stem.startswith(h + "."):
                return i
        return len(LOAD_ORDER_HINT)

    order: List[str] = []
    visiting = set()

    def visit(f: str):
        if f in order or f in visiting:
            return
        visiting.add(f)
        for d in sorted(deps[f], key=hint):
            visit(d)
        visiting.discard(f)
        order.append(f)

    for f in sorted(files, key=lambda x: (hint(x), x)):
        visit(f)
    return order


def unresolved_imports(lib_dir: str, search: List[str]) -> Dict[str, List[str]]:
    """Для каждой DLL из папки: импорты, которых нет ни в одной папке поиска."""
    out = {}
    for f in dependency_order(lib_dir):
        try:
            info = pe_imports(os.path.join(lib_dir, f))
        except (OSError, PEError, struct.error, ValueError) as e:
            out[f] = [f"(не разобрать: {e})"]
            continue
        missing = [n for n in list(info["imports"]) + list(info["delay"])
                   if not is_api_set(n) and find_in(n, [lib_dir] + search) is None]
        if missing:
            out[f] = missing
    return out


# ---------------- --diagnose ----------------

def _load(path: str) -> str:
    import ctypes
    try:
        ctypes.CDLL(path, winmode=0)  # так же, как грузит llama_cpp
        return "OK"
    except OSError as e:
        code = getattr(e, "winerror", None)
        meaning = WIN_ERRORS.get(code, "")
        return f"ОШИБКА WinError {code}{': ' + meaning if meaning else ''} ({e})"


def _nvcuda_lines() -> List[str]:
    lines = []
    path = find_in("nvcuda.dll", system_dirs())
    lines.append(f"nvcuda.dll (драйвер NVIDIA): {path or 'НЕ НАЙДЕН в System32'}")
    if path and sys.platform == "win32":
        import ctypes
        try:
            lib = ctypes.WinDLL(path)
            ver = ctypes.c_int(0)
            rc = lib.cuDriverGetVersion(ctypes.byref(ver))
            v = ver.value
            lines.append(f"  загрузка OK, cuDriverGetVersion={rc}: драйвер поддерживает CUDA до {v // 1000}.{v % 1000 // 10}")
        except OSError as e:
            lines.append(f"  загрузка: ОШИБКА WinError {getattr(e, 'winerror', None)} ({e})")
        except AttributeError as e:
            lines.append(f"  нет cuDriverGetVersion: {e}")
    from .llm import hardware_probe as hp
    errors = []
    for exe in hp._nvidia_smi_candidates():
        try:
            out = subprocess.run([exe, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=10, **hp._run_quiet_kwargs())
        except Exception as e:
            errors.append(f"{exe}: {type(e).__name__}: {e}")
            continue
        if out.returncode == 0 and out.stdout.strip():
            lines.append(f"nvidia-smi ({exe}): имя, версия драйвера, память: {out.stdout.strip()}")
            return lines
        errors.append(f"{exe}: код {out.returncode} {out.stderr.strip()[:200]}")
    lines.append(f"nvidia-smi: не ответил ({'; '.join(errors)})")
    return lines


def _where(name: str, dirs: List[str]) -> str:
    path = find_in(name, dirs)
    return path or "НЕТ"


def diagnose() -> str:
    """Текст отчёта: что есть, чего не хватает и что именно не грузится."""
    import platform
    lines = [
        "Диагностика локальной модели (llama_cpp)",
        f"Python {sys.version.split()[0]} {platform.architecture()[0]}, {platform.platform()}",
        f"Программа: {sys.executable}, собрана: {bool(getattr(sys, 'frozen', False))}, _MEIPASS: {getattr(sys, '_MEIPASS', '-')}",
        "",
    ]
    path_before = os.environ.get("PATH", "")
    added = prepare_search_path()
    pkg, lib = llama_cpp_dir(), llama_cpp_lib_dir()
    lines += [
        f"llama_cpp: версия {llama_cpp_version()}, папка {pkg or 'НЕ НАЙДЕНА'}",
        f"Папка библиотек: {lib or '-'}",
        f"Добавлено в PATH перед импортом: {added or 'ничего'}",
    ]
    if lib and os.path.isdir(lib):
        lines.append("Файлы в папке библиотек:")
        for f in sorted(os.listdir(lib)):
            full = os.path.join(lib, f)
            lines.append(f"  {f}  {os.path.getsize(full)} байт")
    lines.append("")

    search = loader_search_dirs()
    lines.append("Системные библиотеки VC++ (где их найдёт Windows):")
    for name in VC_RUNTIME:
        lines.append(f"  {name}: {_where(name, search)}")
    lines.append("CUDA:")
    cuda_env = {k: v for k, v in os.environ.items() if k.upper() == "CUDA_PATH" or k.upper().startswith("CUDA_PATH_V")}
    lines.append(f"  CUDA_PATH и версии: {cuda_env or 'не заданы (CUDA Toolkit не установлен)'}")
    for pattern in ("cudart64_*.dll", "cublas64_*.dll", "cublasLt64_*.dll"):
        found = []
        for d in [lib or ""] + search:
            found += glob.glob(os.path.join(d, pattern)) if d else []
        lines.append(f"  {pattern}: {sorted(set(found)) or 'НЕ НАЙДЕНЫ ни в папке программы, ни в PATH'}")
    lines += ["  " + s for s in _nvcuda_lines()]
    lines.append("")

    if lib and os.path.isdir(lib):
        lines.append("Зависимости каждой DLL (из таблицы импорта):")
        for f in dependency_order(lib):
            try:
                info = pe_imports(os.path.join(lib, f))
            except (OSError, PEError, struct.error, ValueError) as e:
                lines.append(f"  {f}: не разобрать ({e})")
                continue
            parts = []
            for n in list(info["imports"]) + [f"{d} (отложенно)" for d in info["delay"]]:
                bare = n.split(" ")[0]
                if is_api_set(bare):
                    continue
                where = find_in(bare, [lib] + search)
                parts.append(f"{n} → {where if where else 'НЕ НАЙДЕНА'}")
            lines.append(f"  {f} [{info['machine']}]:")
            lines += [f"      {p}" for p in parts]
        lines.append("")
        lines.append("Загрузка по очереди через ctypes (winmode=0, как в llama_cpp):")
        sequence = []
        for name in CUDA_RUNTIME:  # сначала библиотеки CUDA, которые нашлись, затем DLL llama_cpp
            where = find_in(name, [lib] + search)
            if where:
                sequence.append((name, where))
        taken = {n.lower() for n, _ in sequence}
        sequence += [(f, os.path.join(lib, f)) for f in dependency_order(lib) if f.lower() not in taken]
        first_failure = ""
        for name, full in sequence:
            result = _load(full) if sys.platform == "win32" else "пропущено, не Windows"
            lines.append(f"  {name}: {result}")
            if result.startswith("ОШИБКА") and not first_failure:
                first_failure = f"{name}: {result}"
        lines.append(f"Первая DLL, которая не загрузилась: {first_failure or 'нет'}")
        lines.append("")

    lines.append("import llama_cpp:")
    try:
        import llama_cpp
        lines.append(f"  OK, версия {getattr(llama_cpp, '__version__', '?')}, "
                     f"GPU: {llama_cpp.llama_supports_gpu_offload()}, файл {llama_cpp.__file__}")
    except BaseException:
        lines += ["  " + s for s in traceback.format_exc().rstrip().splitlines()]
    lines += ["", f"PATH до подготовки: {path_before}"]
    return "\n".join(lines)


def cli() -> str:
    """--diagnose: отчёт в diagnose.txt рядом с данными приложения. Возвращает путь."""
    from . import config
    text = diagnose()
    os.makedirs(config.APP_DATA_DIR, exist_ok=True)
    path = os.path.join(config.APP_DATA_DIR, "diagnose.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        print(text)
    except Exception:
        pass
    return path
