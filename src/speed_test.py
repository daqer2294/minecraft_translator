# src/speed_test.py
"""
Тест скорости на 100 строках выбранной сборки: два профиля видеокарты и «только Ollama».
Запуск из собранного приложения, Python не нужен:
    MinecraftTranslator.exe --speed-test
    MinecraftTranslator.exe --speed-test "C:\\Users\\<имя>\\curseforge\\minecraft\\Instances\\<сборка>"
    MinecraftTranslator.exe --speed-test --profile ollama [--ollama-model qwen2.5:3b]
Без пути берутся 100 строк, зашитых в приложение (Cobblemon SMP и ATM10), с путём из модов сборки.
Профили auto и fast_cpu считают встроенной моделью (llama_cpp) и пропускаются, если она не
загрузилась. Профиль ollama: основная модель и сильная (strong_model) обе в Ollama, как в режиме
«Локальная Ollama» приложения; короткие и длинные строки замеряются отдельно.
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
from .llm import ollama_probe
from .llm.base import LLMClientError
from .processors import jar_lang
from .processors.jar_lang import _LANG_MEMBER, _nested_jar_names, _open_nested
from .translators import Translator, _has_latin, _is_bare_id
from .utils.cache import TranslationCache
from .utils.helpers import is_untranslatable, parse_lang_bytes
from .utils.markup import text_word_count

PROFILES = ("auto", "fast_cpu")  # встроенная модель (llama_cpp)
ALL_PROFILES = PROFILES + ("ollama",)
OLLAMA_TITLE = "Только Ollama: основная и сильная модели в Ollama, как в режиме «Локальная Ollama»"
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
        # Не «_stop»: так называется внутренний метод threading.Thread в Python 3.11, и join() его вызывает.
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
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
            self._halt.wait(1.0)

    def stop(self):
        self._halt.set()


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


class Skipped(Exception):
    """Профиль нельзя честно замерить на этом ПК: причина уходит в отчёт."""


def _warm_up(client) -> float:
    """Загрузить модель до замера: первый запрос в Ollama ждёт, пока она загрузится в память."""
    started = time.perf_counter()
    try:
        client.chat([{"role": "user", "content": "Hi"}], max_tokens=4, temperature=0.0)
    except LLMClientError:
        pass  # оборванный по лимиту ответ тоже значит, что модель загружена
    return round(time.perf_counter() - started, 1)


def ollama_main_model(names: List[str], wanted: Optional[str] = None) -> Optional[str]:
    """Какую модель режим «Локальная Ollama» возьмёт основной: выбранную или подсказанную по списку."""
    wanted = (wanted or getattr(config.PROVIDER, "ollama_model", "") or "").strip()
    if wanted:
        return wanted if wanted in names else None
    return ollama_probe.suggest_model([ollama_probe.OllamaModel(n) for n in names])


def run_ollama_profile(strings: List[str], base_input: str, log=lambda s: None,
                       model: Optional[str] = None) -> Dict[str, object]:
    """
    «Только Ollama»: те же клиенты, что собирает приложение в режиме ollama. Короткие строки идут
    основной модели, тексты от 15 слов и второй проход сильной. Части замеряются по отдельности,
    перед каждой модель загружается (время загрузки в отчёте отдельно).
    """
    cfg = copy.copy(config.PROVIDER)
    cfg.mode = "ollama"
    cfg.ollama_base_url = cfg.ollama_base_url or ollama_probe.OLLAMA_DEFAULT_URL
    status = ollama_probe.probe_ollama(cfg.ollama_base_url)
    if not status.available:
        raise Skipped(f"Ollama не отвечает на {cfg.ollama_base_url}: {status.error}")
    names = [m.name for m in status.models]
    name = ollama_main_model(names, model)
    if not name:
        raise Skipped(f"основной модели {model or cfg.ollama_model or ''} нет в Ollama (есть: {names or 'ничего'}); "
                      f"{ollama_probe.recommended_pull_cmd()}")
    cfg.ollama_model = name
    primary, complex_client = factory.build_clients(cfg)
    strong_getter = factory.shared_strong_client(cfg)
    strong, reason = strong_getter()
    tr = Translator(primary, TranslationCache(os.path.join(tempfile.mkdtemp(prefix="mct_speed_"), "c.json")),
                    strict=True, complex_client=complex_client, log=log, target_lang="ru_ru",
                    long_text_client=strong_getter)
    jars = sorted(glob.glob(os.path.join(base_input, "mods", "*.jar"))) if base_input else []
    tr.set_glossary(build_glossary(jars, "ru_ru", base_input or "", ""))
    threshold = int(getattr(config, "LONG_TEXT_MIN_WORDS", 15))
    parts = {
        "short": [t for t in strings if text_word_count(t) < threshold],
        "long": [t for t in strings if text_word_count(t) >= threshold],
    }
    long_client = strong if strong is not None and getattr(config, "LONG_TEXT_STRONG", True) else primary
    sampler = VramSampler()
    sampler.start()
    result: Dict[str, object] = {
        "main_model": name,
        "strong_model": getattr(strong, "model", None) or f"нет: {reason}",
        "strings": len(strings),
    }
    started = time.perf_counter()
    for part, texts in parts.items():
        client = primary if part == "short" else long_client
        result[f"{part}_model"] = getattr(client, "model", None)
        result[f"{part}_warmup_seconds"] = _warm_up(client) if texts else 0.0
        keys = [f"{part}{i}" for i in range(len(texts))]
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            out = jar_lang._translate_strings(dict(zip(keys, texts)), keys, tr, "ru_ru", None)
        sec = time.perf_counter() - t0
        result[f"{part}_strings"] = len(texts)
        result[f"{part}_seconds"] = round(sec, 1)
        result[f"{part}_strings_per_minute"] = round(len(texts) / sec * 60, 1) if sec and texts else 0.0
        result[f"{part}_translated"] = sum(1 for k, t in zip(keys, texts) if out.get(k) != t)
    result["to_second_pass"] = len(tr._rejected)
    t0 = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        result["fixed_by_second_pass"] = (
            mirrorer._second_pass(tr, strong_getter, log, None, None, None) if strong is not None else 0
        )
    result["second_pass_seconds"] = round(time.perf_counter() - t0, 1)
    wall = time.perf_counter() - started
    sampler.stop()
    sampler.join(3)
    result["seconds_total"] = round(wall, 1)
    result["strings_per_minute"] = round(len(strings) / wall * 60, 1) if wall else 0.0
    result["vram_used_mb_max"] = max(sampler.samples) if sampler.samples else None
    result["vram_total_mb"] = sampler.total or None
    ps = _ollama("/api/ps")
    result["ollama_ps"] = [
        {"name": m.get("name"), "size_mb": round(m.get("size", 0) / 2**20), "size_vram_mb": round(m.get("size_vram", 0) / 2**20)}
        for m in (ps or {}).get("models", [])
    ]
    del tr, primary, complex_client, strong
    gc.collect()
    return result


def strong_threads(strings: List[str], threads=(1, 4)) -> List[str]:
    """
    Сильная модель на длинных строках в 1 и 4 потока. Даёт что-то, только если Ollama запущена
    с OLLAMA_NUM_PARALLEL больше 1. На Mac при 4 было 147 → 111 с, но ответы совпали 33 из 40.
    """
    from concurrent.futures import ThreadPoolExecutor

    strong, reason = factory.build_strong_client(config.PROVIDER)
    if strong is None:
        return [f"Сильная модель в потоках: пропущено, {reason}"]
    texts = [s for s in strings if text_word_count(s) >= 15]
    lines, outputs = [], {}
    for n in threads:
        tr = Translator(strong, TranslationCache(os.path.join(tempfile.mkdtemp(prefix="mct_threads_"), "c.json")),
                        strict=True, log=lambda s: None, target_lang="ru_ru")
        started = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()), ThreadPoolExecutor(n) as pool:
            outputs[n] = list(pool.map(lambda t: tr.translate(t, client=strong), texts))
        sec = time.perf_counter() - started
        same = sum(a == b for a, b in zip(outputs[threads[0]], outputs[n]))
        lines.append(
            f"Сильная модель, {n} поток(а): {len(texts)} строк за {sec:.1f} с, {len(texts) / sec * 60:.1f} строк/мин, "
            f"совпало с 1 потоком {same}/{len(texts)}"
        )
    return lines


def report(base_input: Optional[str], limit: Optional[int] = None, log=lambda s: None,
           profiles=ALL_PROFILES, ollama_model: Optional[str] = None) -> str:
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
        *([f"Почему llama_cpp не загрузилась:", *("  " + x for x in hp.llama_cpp_error(full=True).rstrip().splitlines())]
          if hp.llama_cpp_error() else []),
        f"Ollama: {(_ollama('/api/version') or {}).get('version', 'не найдена')}",
        f"Строк: {len(strings)}, из них от 15 слов: {sum(text_word_count(s) >= 15 for s in strings)}",
        "",
    ]
    local_ok = hp.gpu_offload_supported() is not None
    for profile in profiles:
        log(f"Профиль {profile}…")
        title = OLLAMA_TITLE if profile == "ollama" else factory.GPU_PROFILES[profile]
        try:
            if profile == "ollama":
                r = run_ollama_profile(strings, base_input, log, ollama_model)
            elif not local_ok:
                raise Skipped(f"встроенная модель не работает, llama_cpp не загрузилась: {hp.llama_cpp_error()}")
            else:
                r = run_profile(profile, strings, base_input, log)
            lines.append(f"[{profile}] {title}")
            lines += [f"  {k}: {v}" for k, v in r.items() if k != "profile"]
        except Skipped as e:
            lines.append(f"[{profile}] {title}: пропущено, {e}")
        except Exception as e:
            lines.append(f"[{profile}] ОШИБКА: {type(e).__name__}: {e}")
        lines.append("")
    if any(p in PROFILES for p in profiles):
        try:
            lines += strong_threads(strings)
        except Exception as e:
            lines.append(f"Сильная модель в потоках: ОШИБКА: {type(e).__name__}: {e}")
    path = os.path.join(config.APP_DATA_DIR, "speed_test.txt")
    os.makedirs(config.APP_DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _value(argv: List[str], flag: str) -> Optional[str]:
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def cli(argv: List[str]) -> str:
    """
    --speed-test [папка сборки] [--limit N] [--profile auto,fast_cpu,ollama] [--ollama-model ИМЯ].
    Без папки берётся встроенный набор строк, без --profile идут все профили.
    """
    i = argv.index("--speed-test")
    base = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else None
    limit = int(_value(argv, "--limit")) if "--limit" in argv else None
    if base is not None and not os.path.isdir(base):
        raise SystemExit(f"Папка сборки не найдена: {base!r}")
    profiles = tuple(p.strip() for p in (_value(argv, "--profile") or ",".join(ALL_PROFILES)).split(",") if p.strip())
    unknown = [p for p in profiles if p not in ALL_PROFILES]
    if unknown:
        raise SystemExit(f"Неизвестный профиль {unknown}; есть: {', '.join(ALL_PROFILES)}")
    return report(base, limit, profiles=profiles, ollama_model=_value(argv, "--ollama-model"))
