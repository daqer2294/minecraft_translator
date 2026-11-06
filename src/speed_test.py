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
«Локальная Ollama» приложения.
Каждый профиль гоняет весь конвейер: модели загружаются до замера, затем короткие строки и тексты
от 15 слов (отдельно, в fast_cpu одновременно), второй проход по браку, итог. В отчёте откаты по
причинам и по типу строки, ошибки модели и оценка времени на ATM10 (или на указанную сборку).
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
from .llm.base import LLMTruncatedError
from .processors import jar_lang
from .processors.jar_lang import _LANG_MEMBER, _nested_jar_names, _open_nested
from .translators import Translator, _has_latin, _is_bare_id, _looks_russian_only
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


class Skipped(Exception):
    """Профиль нельзя честно замерить на этом ПК: причина уходит в отчёт."""


# Почему строка вышла из первого прохода без перевода. Ключи проверок — Translator._rejection_reason.
REASON_TITLES = {
    "error": "ошибка модели или связи",
    "empty": "пустой ответ",
    "cjk": "иероглифы или другой чужой алфавит",
    "mixed_script": "смесь алфавитов в слове",
    "prompt_leak": "утечка промпта",
    "too_long": "ответ намного длиннее короткого исходника",
    "intro": "вступление или сырая маска [[...]]",
    "foreign_latin": "лишние латинские слова",
    "code": "испорчен код или идентификатор",
    "tags": "придуманные теги",
    "no_cyrillic": "ответ без кириллицы",
    "leftover_english": "недоперевод латиницей",
    "glossary": "потерян термин глоссария",
    "tokens": "не совпали плейсхолдеры и теги",
    "unchanged": "модель вернула исходник, проверки его пропустили",
    "rule_skip": "не переводится по правилам (ID, имя файла, без латиницы)",
}


def _model_label(client) -> Optional[str]:
    return None if client is None else (getattr(client, "model", None) or getattr(client, "name", None))


def _load(client, role: str) -> float:
    """
    Загрузить модель до замера и вернуть, сколько это заняло. Раньше загрузка шла внутри первого
    запроса и попадала в скорость. Ошибку не глотает: без ответа модели замер ничего не значит.
    """
    started = time.perf_counter()
    try:
        client.chat([{"role": "user", "content": "Hi"}], max_tokens=4, temperature=0.0)
    except LLMTruncatedError:
        pass  # ответ оборван по лимиту, но модель загружена и отвечает
    except Exception as e:
        raise RuntimeError(f"{role} модель {_model_label(client)} не ответила: {type(e).__name__}: {e}") from e
    return round(time.perf_counter() - started, 1)


def _translator(primary, complex_client, strong_getter, base_input: str, log) -> Translator:
    tr = Translator(primary, TranslationCache(os.path.join(tempfile.mkdtemp(prefix="mct_speed_"), "c.json")),
                    strict=True, complex_client=complex_client, log=log, target_lang="ru_ru",
                    long_text_client=strong_getter)
    jars = sorted(glob.glob(os.path.join(base_input, "mods", "*.jar"))) if base_input else []
    tr.set_glossary(build_glossary(jars, "ru_ru", base_input or "", ""))
    return tr


def _translate_part(tr, texts: List[str], prefix: str):
    keys = [f"{prefix}{i}" for i in range(len(texts))]
    started = time.perf_counter()
    out = {}
    if texts:
        with contextlib.redirect_stdout(io.StringIO()):
            out = jar_lang._translate_strings(dict(zip(keys, texts)), keys, tr, "ru_ru", None)
    return [out.get(k, t) for k, t in zip(keys, texts)], time.perf_counter() - started


def _skipped_by_rule(text: str) -> bool:
    return not text or _looks_russian_only(text) or not _has_latin(text) or _is_bare_id(text) or is_untranslatable(text)


def measure(tr: Translator, strings: List[str], primary, strong, strong_reason: str, strong_getter,
            log=lambda s: None, parallel: bool = False) -> Dict[str, object]:
    """
    Весь конвейер приложения на наборе строк: первый проход (короткие основной моделью, тексты от
    LONG_TEXT_MIN_WORDS слов сильной), второй проход сильной моделью по браку основной, итог.
    parallel=True: короткие и длинные идут одновременно, как в профиле fast_cpu приложения.
    Время каждой части считается от её начала до её конца; модели загружены заранее.
    """
    threshold = int(getattr(config, "LONG_TEXT_MIN_WORDS", 15))
    parts = {
        "short": [t for t in strings if text_word_count(t) < threshold],
        "long": [t for t in strings if text_word_count(t) >= threshold],
    }
    long_client = strong if strong is not None and getattr(config, "LONG_TEXT_STRONG", True) else primary
    tr.outcomes, tr.errors = {}, {}
    tr.overlap_strong = False  # одновременность задаёт parallel: так у каждой части своё время
    result: Dict[str, object] = {
        "main_model": _model_label(primary),
        "strong_model": _model_label(strong) or f"нет: {strong_reason}",
        "long_texts_model": _model_label(long_client),
        "strings": len(strings),
        "load_seconds_main": _load(primary, "Основная"),
        "load_seconds_strong": _load(strong, "Сильная") if strong is not None else 0.0,
    }
    outputs, seconds = {}, {}
    started = time.perf_counter()
    if parallel:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(2, thread_name_prefix="speed") as pool:
            futures = {p: pool.submit(_translate_part, tr, texts, p) for p, texts in parts.items()}
            for p, f in futures.items():
                outputs[p], seconds[p] = f.result()
    else:
        for p, texts in parts.items():
            outputs[p], seconds[p] = _translate_part(tr, texts, p)
    first = time.perf_counter() - started
    reasons = dict(tr.outcomes)
    rejected = {text for text, _ in tr._rejected}  # то, что получит второй проход
    second_started = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        fixed = mirrorer._second_pass(tr, strong_getter, log, None, None, None) if strong is not None and rejected else 0
    second = time.perf_counter() - second_started
    total = time.perf_counter() - started

    breakdown: Dict[str, Dict[str, int]] = {}
    totals = {"translated_first_pass": 0, "to_second_pass": 0, "translated_final": 0}
    for p, texts in parts.items():
        ok_first = ok_final = to_second = 0
        for text, out in zip(texts, outputs[p]):
            if out != text:
                ok_first += 1
                ok_final += 1
                continue
            cached = tr.cache.get(text, "ru_ru")
            if cached is not None and cached != text:
                ok_final += 1  # перевёл второй проход
            reason = "rule_skip" if _skipped_by_rule(text) else reasons.get(text, "unchanged")
            row = breakdown.setdefault(reason, {"short": 0, "long": 0, "to_second_pass": 0})
            row[p] += 1
            if text in rejected:
                row["to_second_pass"] += 1
                to_second += 1
        n = len(texts)
        result[f"{p}_strings"] = n
        result[f"{p}_seconds"] = round(seconds[p], 1)
        result[f"{p}_strings_per_minute"] = round(n / seconds[p] * 60, 1) if n and seconds[p] else 0.0
        result[f"{p}_translated_first_pass"] = ok_first
        result[f"{p}_rolled_back_first_pass"] = n - ok_first
        result[f"{p}_to_second_pass"] = to_second
        result[f"{p}_translated_final"] = ok_final
        totals["translated_first_pass"] += ok_first
        totals["to_second_pass"] += to_second
        totals["translated_final"] += ok_final
    result.update({
        "parallel_parts": parallel,
        "seconds_first_pass": round(first, 1),
        "seconds_second_pass": round(second, 1),
        "seconds_total": round(total, 1),
        "strings_per_minute": round(len(strings) / total * 60, 1) if total else 0.0,
        "translated_first_pass": totals["translated_first_pass"],
        "rolled_back_first_pass": len(strings) - totals["translated_first_pass"],
        "to_second_pass": totals["to_second_pass"],
        "fixed_by_second_pass": fixed,
        "translated_final": totals["translated_final"],
        "rollback_reasons": breakdown,
        "model_errors": dict(sorted(tr.errors.items(), key=lambda kv: -kv[1])[:5]),
    })
    return result


def _ollama_state(sampler: "VramSampler") -> Dict[str, object]:
    ps = _ollama("/api/ps")
    return {
        "vram_used_mb_max": max(sampler.samples) if sampler.samples else None,
        "vram_used_mb_min": min(sampler.samples) if sampler.samples else None,
        "vram_total_mb": sampler.total or None,
        "ollama_ps": [
            {"name": m.get("name"), "size_mb": round(m.get("size", 0) / 2**20), "size_vram_mb": round(m.get("size_vram", 0) / 2**20)}
            for m in (ps or {}).get("models", [])
        ],
    }


def run_profile(profile: str, strings: List[str], base_input: str, log=lambda s: None) -> Dict[str, object]:
    """Профиль встроенной модели (auto, fast_cpu) с теми же клиентами, что собирает приложение."""
    cfg = copy.copy(config.PROVIDER)
    cfg.gpu_profile = profile
    primary, complex_client = factory.build_clients(cfg)
    strong_getter = factory.shared_strong_client(cfg)
    strong, reason = strong_getter()
    tr = _translator(primary, complex_client, strong_getter, base_input, log)
    sampler = VramSampler()
    sampler.start()
    try:
        result = measure(tr, strings, primary, strong, reason, strong_getter, log, parallel=profile == "fast_cpu")
    finally:
        sampler.stop()
        sampler.join(3)
    result["main_client"] = primary.name
    # -1: все слои на видеокарте, 0: процессор. На Windows решает сохранённая проба железа.
    result["main_gpu_layers"] = getattr(primary, "n_gpu_layers", None)
    result.update(_ollama_state(sampler))
    del tr, primary, complex_client, strong
    gc.collect()
    return result


def ollama_main_model(names: List[str], wanted: Optional[str] = None) -> Optional[str]:
    """Какую модель режим «Локальная Ollama» возьмёт основной: выбранную или подсказанную по списку."""
    wanted = (wanted or getattr(config.PROVIDER, "ollama_model", "") or "").strip()
    if wanted:
        return wanted if wanted in names else None
    return ollama_probe.suggest_model([ollama_probe.OllamaModel(n) for n in names])


def run_ollama_profile(strings: List[str], base_input: str, log=lambda s: None,
                       model: Optional[str] = None) -> Dict[str, object]:
    """«Только Ollama»: те же клиенты, что собирает приложение в режиме ollama, тот же замер."""
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
    tr = _translator(primary, complex_client, strong_getter, base_input, log)
    sampler = VramSampler()
    sampler.start()
    try:
        result = measure(tr, strings, primary, strong, reason, strong_getter, log)
    finally:
        sampler.stop()
        sampler.join(3)
    result.update(_ollama_state(sampler))
    del tr, primary, complex_client, strong
    gc.collect()
    return result


def pack_counts(base_input: Optional[str]) -> Dict[str, object]:
    """
    Сколько строк сборки пойдёт в модели с пустым кэшем: подсчёт «Только посчитать» без модели.
    Без папки сборки — числа ATM10, посчитанные так же и записанные рядом со встроенными строками.
    """
    if not base_input:
        with open(BUNDLED_PATH, encoding="utf-8") as f:
            return dict(json.load(f)["atm10"])
    from . import estimate
    est = estimate.estimate_dir(base_input, "ru_ru", os.path.join(tempfile.mkdtemp(), "нет.json"),
                                strong_available=True, log=lambda s: None)
    by_len = {b: est.to_fast[b] + est.to_strong[b] for b in estimate.BUCKETS}
    return {
        "pack": os.path.basename(os.path.normpath(base_input)),
        "short": by_len[estimate.BUCKETS[0]] + by_len[estimate.BUCKETS[1]],
        "long": by_len[estimate.BUCKETS[2]],
        "how": "«Только посчитать» по этой папке с пустым кэшем",
    }


def pack_estimate(r: Dict[str, object], counts: Dict[str, object]) -> List[str]:
    """
    Время на всю сборку по этому замеру: скорость каждой части и доля строк во второй проход
    берутся из результатов профиля, число строк — из подсчёта сборки. Ничего не допускается.
    """
    ns, nl = int(counts["short"]), int(counts["long"])
    lines = [f"  Оценка {counts['pack']} ({counts['how']}: {ns} строк до 15 слов, {nl} от 15) по этому замеру:"]
    if r.get("model_errors"):
        lines.append("    не считается: в замере были ошибки модели, скорость и откаты ненастоящие")
        return lines
    per = {}
    for p in ("short", "long"):
        n = int(r.get(f"{p}_strings") or 0)
        if not n:
            lines.append(f"    не считается: в наборе нет {'коротких' if p == 'short' else 'длинных'} строк")
            return lines
        per[p] = {
            "sec": float(r[f"{p}_seconds"]) / n,
            "second_share": int(r[f"{p}_to_second_pass"]) / n,
            "left_share": (n - int(r[f"{p}_translated_final"])) / n,
            "rolled_share": int(r[f"{p}_rolled_back_first_pass"]) / n,
        }
    to_second = int(r.get("to_second_pass") or 0)
    sec_second = float(r["seconds_second_pass"]) / to_second if to_second else 0.0
    t_short, t_long = ns * per["short"]["sec"], nl * per["long"]["sec"]
    first = max(t_short, t_long) if r.get("parallel_parts") else t_short + t_long
    n_second = ns * per["short"]["second_share"] + nl * per["long"]["second_share"]
    second = n_second * sec_second

    def fmt(sec: float) -> str:
        return f"{sec / 60:.0f} мин" if sec < 5400 else f"{sec / 3600:.1f} ч"

    lines += [
        f"    короткие: {per['short']['sec']:.2f} с на строку → {fmt(t_short)}; длинные: {per['long']['sec']:.2f} с → {fmt(t_long)}"
        + (" (идут одновременно)" if r.get("parallel_parts") else ""),
        f"    откат в первом проходе: {per['short']['rolled_share']:.0%} коротких, {per['long']['rolled_share']:.0%} длинных; "
        f"во второй проход около {n_second:.0f} строк по {sec_second:.2f} с → {fmt(second)}",
        f"    всего около {fmt(first + second)}; без перевода останется около "
        f"{ns * per['short']['left_share'] + nl * per['long']['left_share']:.0f} строк",
    ]
    return lines


def format_result(r: Dict[str, object]) -> List[str]:
    lines = [f"  {k}: {v}" for k, v in r.items() if k not in ("profile", "rollback_reasons", "model_errors")]
    reasons = r.get("rollback_reasons") or {}
    if reasons:
        lines.append("  Откаты первого прохода по причинам (коротких / длинных, из них во второй проход):")
        for key, row in sorted(reasons.items(), key=lambda kv: -(kv[1]["short"] + kv[1]["long"])):
            lines.append(f"    {REASON_TITLES.get(key, key)}: {row['short']} / {row['long']}, во второй проход {row['to_second_pass']}")
    for text, count in (r.get("model_errors") or {}).items():
        lines.append(f"  Ошибка модели ({count} раз): {text}")
    return lines


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
    # Как приложение при «Старт»: проба из кэша или новая с сохранением. От сохранённой пробы
    # зависит, пойдёт ли основная модель на видеокарту NVIDIA (n_gpu_layers = -1) или на процессор.
    hw = hp.get_or_probe()
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
    try:
        counts = pack_counts(base_input)
    except Exception as e:
        counts = None
        lines += [f"Оценка времени на сборку не считается: {type(e).__name__}: {e}", ""]
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
            lines += format_result(r)
            if counts is not None:
                lines += pack_estimate(r, counts)
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
