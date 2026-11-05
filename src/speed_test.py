# src/speed_test.py
"""
Тест скорости на 100 строках выбранной сборки: два профиля видеокарты подряд.
Запуск из собранного приложения, Python не нужен:
    MinecraftTranslator.exe --speed-test
    MinecraftTranslator.exe --speed-test "C:\\Users\\<имя>\\curseforge\\minecraft\\Instances\\<сборка>"
Без пути берутся 100 строк, зашитых в приложение (Cobblemon SMP и ATM10), с путём из модов сборки.
Отчёт пишется в <папка данных>\\speed_test.txt. Настоящий кэш не используется и не меняется.
"""
from __future__ import annotations

import contextlib
import copy
import gc
import glob
import io
import json
import os
import platform
import random
import subprocess
import tempfile
import threading
import time
import urllib.request
import zipfile
from typing import Dict, List, Optional

from . import config, mirrorer
from .glossary import build_glossary
from .llm import factory
from .llm import hardware_probe as hp
from .processors import jar_lang
from .processors.jar_lang import _LANG_MEMBER, _nested_jar_names, _open_nested
from .translators import Translator, _has_latin, _is_bare_id
from .utils.cache import TranslationCache
from .utils.helpers import is_untranslatable, parse_lang_bytes
from .utils.markup import text_word_count

PROFILES = ("auto", "fast_cpu")
BUNDLED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "speed_test_strings.json")


def bundled_strings() -> List[str]:
    """100 строк из Cobblemon SMP и ATM10, которые идут вместе с приложением."""
    with open(BUNDLED_PATH, encoding="utf-8") as f:
        return [item["text"] for item in json.load(f)["strings"]]


def collect_strings(base_input: str, n_short: int = 70, n_long: int = 30, seed: int = 100) -> List[str]:
    """Строки из en_us модов сборки: n_short до 14 слов и n_long от 15, одинаковые при каждом запуске."""
    texts = set()

    def walk(zf, depth=0):
        for name in zf.namelist():
            match = _LANG_MEMBER.fullmatch(name.replace("\\", "/"))
            if match and match.group(2).lower() == "en_us":
                for value in (parse_lang_bytes(zf.read(name)) or {}).values():
                    if isinstance(value, str) and _has_latin(value) and not _is_bare_id(value) and not is_untranslatable(value):
                        texts.add(value)
        if depth < 4:
            for name in _nested_jar_names(zf):
                inner = _open_nested(zf, name)
                if inner is not None:
                    walk(inner, depth + 1)

    for path in sorted(glob.glob(os.path.join(base_input, "mods", "*.jar"))):
        try:
            with zipfile.ZipFile(path) as zf:
                walk(zf)
        except (OSError, zipfile.BadZipFile):
            continue
    ordered = sorted(texts)
    rnd = random.Random(seed)
    short = [t for t in ordered if text_word_count(t) < 15]
    long_ = [t for t in ordered if text_word_count(t) >= 15]
    return rnd.sample(short, min(n_short, len(short))) + rnd.sample(long_, min(n_long, len(long_)))


class VramSampler(threading.Thread):
    """Раз в секунду спрашивает nvidia-smi о занятой видеопамяти. Без NVIDIA молчит."""

    def __init__(self):
        super().__init__(daemon=True)
        self.samples: List[int] = []
        self.total = 0
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5, **hp._run_quiet_kwargs(),
                )
                used, total = (int(float(x)) for x in out.stdout.splitlines()[0].split(","))
                self.samples.append(used)
                self.total = total
            except Exception:
                return
            self._stop.wait(1.0)

    def stop(self):
        self._stop.set()


def _ollama(path: str) -> Optional[dict]:
    url = (config.PROVIDER.ollama_base_url or "http://127.0.0.1:11434").rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def run_profile(profile: str, strings: List[str], base_input: str, log=lambda s: None) -> Dict[str, object]:
    cfg = copy.copy(config.PROVIDER)
    cfg.gpu_profile = profile
    primary, complex_client = factory.build_clients(cfg)
    strong_getter = factory.shared_strong_client(cfg)
    strong, reason = strong_getter()
    tr = Translator(primary, TranslationCache(os.path.join(tempfile.mkdtemp(prefix="mct_speed_"), "c.json")),
                    strict=True, complex_client=complex_client, log=log, target_lang="ru_ru",
                    long_text_client=strong_getter)
    jars = sorted(glob.glob(os.path.join(base_input, "mods", "*.jar"))) if base_input else []
    tr.set_glossary(build_glossary(jars, "ru_ru", base_input or "", ""))
    tr.overlap_strong = profile == "fast_cpu"
    sampler = VramSampler()
    sampler.start()
    started = time.perf_counter()
    keys = [f"k{i}" for i in range(len(strings))]
    with contextlib.redirect_stdout(io.StringIO()):
        out = jar_lang._translate_strings(dict(zip(keys, strings)), keys, tr, "ru_ru", None)
    first = time.perf_counter() - started
    rejected = len(tr._rejected)
    ps = _ollama("/api/ps")
    with contextlib.redirect_stdout(io.StringIO()):
        fixed = mirrorer._second_pass(tr, strong_getter, log, None, None, None) if strong is not None else 0
    wall = time.perf_counter() - started
    sampler.stop()
    sampler.join(3)
    translated = sum(1 for k, s in zip(keys, strings) if out.get(k) != s)
    result = {
        "profile": profile,
        "strings": len(strings),
        "seconds_first_pass": round(first, 1),
        "seconds_total": round(wall, 1),
        "strings_per_minute": round(len(strings) / wall * 60, 1) if wall else 0.0,
        "translated_first_pass": translated,
        "rolled_back_first_pass": len(strings) - translated,
        "to_second_pass": rejected,
        "fixed_by_second_pass": fixed,
        "fast_model": primary.name,
        "strong_model": getattr(strong, "name", None) or f"нет: {reason}",
        "vram_used_mb_max": max(sampler.samples) if sampler.samples else None,
        "vram_used_mb_min": min(sampler.samples) if sampler.samples else None,
        "vram_total_mb": sampler.total or None,
        "ollama_ps": [
            {"name": m.get("name"), "size_mb": round(m.get("size", 0) / 2**20), "size_vram_mb": round(m.get("size_vram", 0) / 2**20)}
            for m in (ps or {}).get("models", [])
        ],
    }
    del tr, primary, complex_client, strong
    gc.collect()
    return result


def report(base_input: Optional[str], limit: Optional[int] = None, log=lambda s: None) -> str:
    strings = collect_strings(base_input) if base_input else bundled_strings()
    if limit:
        strings = strings[:limit // 2] + strings[-(limit - limit // 2):]
    hw = hp.probe_hardware()
    lines = [
        f"Тест скорости Minecraft Translator, {time.strftime('%Y-%m-%d %H:%M')}",
        f"Строки: {base_input or 'встроенный набор (Cobblemon SMP и ATM10)'}",
        f"Система: {platform.platform()} | процессор: {platform.processor()}",
        f"Железо: {hw.summary()}",
        f"Перевод: {hp.inference_device(hw)} | llama_cpp умеет GPU: {hp.gpu_offload_supported()}",
        f"Ollama: {(_ollama('/api/version') or {}).get('version', 'не найдена')}",
        f"Строк: {len(strings)}, из них от 15 слов: {sum(text_word_count(s) >= 15 for s in strings)}",
        "",
    ]
    for profile in PROFILES:
        log(f"Профиль {profile}…")
        try:
            r = run_profile(profile, strings, base_input, log)
            lines.append(f"[{profile}] {factory.GPU_PROFILES[profile]}")
            lines += [f"  {k}: {v}" for k, v in r.items() if k != "profile"]
        except Exception as e:
            lines.append(f"[{profile}] ОШИБКА: {type(e).__name__}: {e}")
        lines.append("")
    path = os.path.join(config.APP_DATA_DIR, "speed_test.txt")
    os.makedirs(config.APP_DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def cli(argv: List[str]) -> str:
    """--speed-test [папка сборки] [--limit N]. Без папки берётся встроенный набор строк."""
    i = argv.index("--speed-test")
    base = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else None
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    if base is not None and not os.path.isdir(base):
        raise SystemExit(f"Папка сборки не найдена: {base!r}")
    return report(base, limit)
