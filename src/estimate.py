# src/estimate.py
"""
«Только посчитать»: проход по сборке тем же конвейером, но без модели. Вместо модели
отвечают заглушки, которые считают запросы и возвращают исходный текст. Ничего не
переводится и не пишется в кэш и в папку результата.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import config, mirrorer
from .translators import Translator, _has_latin, _is_bare_id, _is_complex_text, _looks_russian_only
from .utils.cache import TranslationCache
from .utils.helpers import is_untranslatable
from .utils.markup import text_word_count

BUCKETS = ("до 3 слов", "4–14 слов", "15+ слов")

# Секунды на строку с проверками и записью кэша, замер на Mac M2: Qwen 2.5 3B в процессе (Metal),
# llama3.1 8B в Ollama, строки Cobblemon по 100 на длину. Основная на 15+ словах: 100 описаний.
# Строка в пачке: пачки по 25. Для другого железа это только порядок величины.
MEASURED_SECONDS = {
    "fast": {"до 3 слов": 0.31, "4–14 слов": 0.67, "15+ слов": 1.24},
    "fast_batch_item": 0.56,
    "strong": {"до 3 слов": 1.08, "4–14 слов": 1.77, "15+ слов": 3.34},
}
# Доля ответов основной модели, ушедших во второй проход, те же замеры.
MEASURED_REJECT_RATE = {"до 3 слов": 0.18, "4–14 слов": 0.19, "15+ слов": 0.19}
MEASURED_ON = "Mac M2, замер 300 строк"
# Полный прогон Cobblemon (v3) занял 61 мин, оценка по тем же числам дала 40 мин: в настоящем
# прогоне больше откатов и повторных запросов. Вторая цифра оценки умножена на это отношение.
FULL_RUN_CORRECTION = 61 / 40


def bucket(text: str) -> str:
    words = text_word_count(text)
    return BUCKETS[0] if words <= 3 else BUCKETS[1] if words <= 14 else BUCKETS[2]


class _EchoClient:
    """Заглушка модели: считает запросы и строки, отвечает исходным текстом."""

    def __init__(self, name: str):
        self.name = name
        self.requests = 0
        self.batch_requests = 0
        self.batch_items = 0
        self._lock = threading.Lock()

    def chat(self, messages, **kwargs) -> str:
        user = messages[-1]["content"]
        with self._lock:
            self.requests += 1
            if "JSON array" in messages[0]["content"]:
                items = json.loads(user)
                self.batch_requests += 1
                self.batch_items += len(items)
                return json.dumps(items, ensure_ascii=False)
        return user


@dataclass
class Estimate:
    total: int = 0
    unique: int = 0
    cached: int = 0
    repeated: int = 0
    nothing_to_translate: int = 0
    to_fast: Dict[str, int] = field(default_factory=lambda: {b: 0 for b in BUCKETS})
    to_strong: Dict[str, int] = field(default_factory=lambda: {b: 0 for b in BUCKETS})
    # Из to_fast: строки, которые translate_many отправит пачкой, а не по одной.
    to_fast_batched: Dict[str, int] = field(default_factory=lambda: {b: 0 for b in BUCKETS})
    fast_requests: int = 0
    fast_batch_requests: int = 0
    fast_batch_items: int = 0
    strong_requests: int = 0
    strong_available: bool = False
    second_pass: int = 0
    seconds: Dict[str, float] = field(default_factory=dict)

    def lines(self) -> List[str]:
        fast, strong = sum(self.to_fast.values()), sum(self.to_strong.values())
        by_len = {b: self.to_fast[b] + self.to_strong[b] for b in BUCKETS}
        total_s = sum(self.seconds.values())
        def minutes(s: float) -> str:
            return f"{s / 60:.0f} мин" if s < 5400 else f"{s / 3600:.1f} ч"
        return [
            f"Строк всего: {self.total}, разных: {self.unique}",
            f"Уже в кэше: {self.cached}, повторы внутри сборки: {self.repeated}, переводить нечего: {self.nothing_to_translate}",
            f"В основную модель: {fast} строк, запросов {self.fast_requests} (пачками {self.fast_batch_requests} на {self.fast_batch_items} строк)",
            f"Сразу в сильную модель: {strong}" + ("" if self.strong_available else " (сильной модели нет, длинные тоже пойдут в основную)"),
            f"Во второй проход ожидается: около {self.second_pass}",
            "По длине (то, что пойдёт в модель): " + ", ".join(f"{b}: {by_len[b]}" for b in BUCKETS),
            f"Оценка времени ({MEASURED_ON}): основная {minutes(self.seconds.get('fast', 0))}, "
            f"сильная {minutes(self.seconds.get('strong', 0))}, второй проход {minutes(self.seconds.get('second_pass', 0))}, "
            f"всего около {minutes(total_s)}; с поправкой по полному прогону Cobblemon около "
            f"{minutes(total_s * FULL_RUN_CORRECTION)}",
        ]


class _CountingTranslator(Translator):
    """Настоящий Translator: те же проверки, кэш, маршруты и пачки. Учёт строк до модели."""

    def __init__(self, *args, estimate: Estimate, real_cached: set, **kwargs):
        super().__init__(*args, **kwargs)
        self.est = estimate
        self._real_cached = real_cached
        self._seen: set = set()
        self._depth = threading.local()
        self._count_lock = threading.Lock()
        # Порядок проходов на число строк и запросов не влияет, а считались бы они дважды.
        self.skip_priority_pass = True

    def _classify(self, text: str, target_lang: str, in_batch_call: bool = False) -> None:
        with self._count_lock:
            self.est.total += 1
            if not text or _looks_russian_only(text) or not _has_latin(text) or _is_bare_id(text) or is_untranslatable(text):
                self.est.nothing_to_translate += 1
                return
            if text in self._real_cached:
                self.est.cached += 1
                return
            if text in self._seen:
                self.est.repeated += 1
                return
            self._seen.add(text)
            self.est.unique += 1
        strong = self._client_for_long_text(text) is not None
        batched = (
            in_batch_call and self.batch_size > 1 and not strong
            and not _is_complex_text(text, self.complex_len_threshold)
        )
        with self._count_lock:
            (self.est.to_strong if strong else self.est.to_fast)[bucket(text)] += 1
            if batched:
                self.est.to_fast_batched[bucket(text)] += 1

    def _remember(self, texts: List[str], target_lang: str) -> None:
        # Повтор той же строки в настоящем прогоне взялся бы из кэша, а не из модели.
        for text in texts:
            if text and text in self._seen:
                self.cache.put(text, text, target_lang)

    def _top(self) -> bool:
        return getattr(self._depth, "value", 0) == 0

    def translate(self, text, target_lang=None, client=None):
        lang = target_lang or self.target_lang
        top = self._top()
        if top:
            self._classify(text, lang)
        self._depth.value = getattr(self._depth, "value", 0) + 1
        try:
            return super().translate(text, target_lang, client)
        finally:
            self._depth.value -= 1
            if top:
                self._remember([text], lang)

    def translate_many(self, texts, target_lang=None):
        lang = target_lang or self.target_lang
        top = self._top()
        if top:
            for text in texts:
                self._classify(text, lang, in_batch_call=True)
        self._depth.value = getattr(self._depth, "value", 0) + 1
        try:
            return super().translate_many(texts, target_lang)
        finally:
            self._depth.value -= 1
            if top:
                self._remember(list(texts), lang)


def _seconds(est: Estimate) -> Dict[str, float]:
    fast = sum(
        (est.to_fast[b] - est.to_fast_batched[b]) * MEASURED_SECONDS["fast"][b]
        + est.to_fast_batched[b] * MEASURED_SECONDS["fast_batch_item"]
        for b in BUCKETS
    )
    strong = sum(est.to_strong[b] * MEASURED_SECONDS["strong"][b] for b in BUCKETS)
    second = sum(est.to_fast[b] * MEASURED_REJECT_RATE[b] * MEASURED_SECONDS["strong"][b] for b in BUCKETS) if est.strong_available else 0.0
    return {"fast": fast, "strong": strong, "second_pass": second}


def estimate_dir(
    base_input: str, target_lang: str, cache_path: str, strong_available: bool,
    log: Callable[[str], None] = print, cancel_event=None,
) -> Estimate:
    est = Estimate(strong_available=strong_available)
    work = tempfile.mkdtemp(prefix="mct_estimate_")
    try:
        cache_copy = os.path.join(work, "cache.json")
        real_cached = set()
        if os.path.isfile(cache_path):
            shutil.copy(cache_path, cache_copy)
            with open(cache_copy, encoding="utf-8") as f:
                data = json.load(f)
            real_cached = set((data.get(target_lang) or {}) if isinstance(data, dict) else ())
        fast, strong = _EchoClient("fast"), _EchoClient("strong")
        tr = _CountingTranslator(
            fast, TranslationCache(cache_copy), strict=True, log=lambda s: None, target_lang=target_lang,
            cancel_event=cancel_event, long_text_client=(lambda: (strong, "")) if strong_available else None,
            estimate=est, real_cached=real_cached,
        )
        tr.max_attempts = 1
        mirrorer.mirror_translate_dir(
            base_input, os.path.join(work, "out"), tr, log=lambda s: log(s) if s.startswith(("[PACK-INFO]", "[GLOSSARY]", "[!]", "[WARN")) else None,
            target_lang=target_lang, cancel_event=cancel_event,
        )
        est.fast_requests, est.fast_batch_requests, est.fast_batch_items = fast.requests, fast.batch_requests, fast.batch_items
        est.strong_requests = strong.requests
        est.second_pass = round(sum(est.to_fast[b] * MEASURED_REJECT_RATE[b] for b in BUCKETS)) if strong_available else 0
        est.seconds = _seconds(est)
        return est
    finally:
        shutil.rmtree(work, ignore_errors=True)
