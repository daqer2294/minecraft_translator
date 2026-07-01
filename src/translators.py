# src/translators.py
from __future__ import annotations

import json
import re
import time
import random
import urllib.error
from typing import Optional, List, Tuple, Dict

from .utils.cache import TranslationCache
from .llm.base import LLMClient, RateLimitError
from . import config

# -------- защитные регекспы --------
RE_PLACEHOLDER = re.compile(
    r"%(?:\d+\$)?-?\d*(?:\.\d+)?[sdifx]|"   # %s, %1$s, %02d
    r"\{[\w\.]+\}"                          # {count}, {0}, {player.name}
)
RE_NAMESPACE = re.compile(r"[A-Za-z0-9_.-]+:[A-Za-z0-9_/.-]+")
RE_LATIN = re.compile(r"[A-Za-z]")
RE_CYR   = re.compile(r"[А-Яа-яЁё]")


def _extract_tokens(s: str) -> Tuple[str, ...]:
    """Достаём плейсхолдеры и namespaced ID из строки."""
    if not s:
        return tuple()
    toks: List[str] = []
    toks.extend(RE_PLACEHOLDER.findall(s))
    toks.extend(RE_NAMESPACE.findall(s))
    return tuple(sorted(toks))


def _looks_russian_only(s: str) -> bool:
    """Строка уже полностью на кириллице (и без латиницы)."""
    return bool(RE_CYR.search(s)) and not RE_LATIN.search(s)


def _has_latin(s: str) -> bool:
    return bool(RE_LATIN.search(s))


def _lang_name_from_mc_code(code: str) -> str:
    """
    Небольшой маппер для человекочитаемого языка в подсказке модели.
    ru_ru -> Russian, de_de -> German и т.п.
    Если не знаем — возвращаем сам код.
    """
    m = code.lower()
    mapping = {
        "ru_ru": "Russian",
        "en_us": "English",
        "de_de": "German",
        "fr_fr": "French",
        "es_es": "Spanish",
        "pt_br": "Brazilian Portuguese",
        "zh_cn": "Simplified Chinese",
        "ja_jp": "Japanese",
    }
    return mapping.get(m, code)


def _coerce_json_array(raw: str, expected_len: int) -> List[str]:
    """
    Пытаемся аккуратно вытащить JSON-массив строк из ответа модели, даже если она
    умничает и добавляет ```json, текст до/после и т.п.
    Бросаем исключение, если вменяемо распарсить не удалось.
    """
    txt = raw.strip()

    # убираем markdown-ограды
    if txt.startswith("```"):
        # типа ```json\n[ ... ]\n```
        txt = txt.strip("`").strip()
        # могли остаться префиксы 'json' / 'JSON'
        if txt.lower().startswith("json"):
            txt = txt[4:].lstrip()

    # пробуем как есть
    try:
        arr = json.loads(txt)
        if isinstance(arr, list) and (expected_len == 0 or len(arr) == expected_len):
            return [str(x) for x in arr]
    except Exception:
        pass

    # пробуем вырезать первый [...последний]
    start = txt.find("[")
    end = txt.rfind("]")
    if start != -1 and end != -1 and end > start:
        core = txt[start : end + 1]
        try:
            arr = json.loads(core)
            if isinstance(arr, list) and (expected_len == 0 or len(arr) == expected_len):
                return [str(x) for x in arr]
        except Exception:
            pass

    # если всё плохо — кидаем ошибку
    raise RuntimeError("Batch output format mismatch")


# =============================================================================
# System-prompt'ы вынесены в МОДУЛЬНЫЕ КОНСТАНТЫ (STEP 4).
#
# Это критично для prefix/prompt caching локальной модели: KV-кэш общего
# префикса переиспользуется только если system-prompt БАЙТ-В-БАЙТ одинаков между
# запросами одного прогона. Держим текст в одном месте, чтобы исключить
# случайные различия (лишний пробел, переставленная строка и т.п.).
#
# Плейсхолдеры языка — __LANG_NAME__ / __LANG_CODE__ — подставляются через
# str.replace (НЕ .format), чтобы не конфликтовать с фигурными скобками в
# примерах ({count}, {0}). В рамках одного прогона target_lang постоянен, поэтому
# итоговая строка тоже постоянна.
# =============================================================================

SINGLE_SYSTEM_PROMPT_TEMPLATE = (
    "You are a professional localization engine for Minecraft mods and modpacks.\n"
    "Translate the following text into __LANG_NAME__ (Minecraft locale: __LANG_CODE__).\n"
    "Hard rules:\n"
    " - Do NOT translate or alter placeholders (e.g., %s, %1$s, {count}, {0}).\n"
    " - Do NOT translate or alter namespaced IDs like modid:item or text keys.\n"
    " - Do NOT leave parts of the text in English unless they are proper names or IDs.\n"
    " - Keep formatting codes (§a, §b, etc.) and JSON fragments intact.\n"
    "Return ONLY the translated text, without quotes or explanations."
)

BATCH_SYSTEM_PROMPT_TEMPLATE = (
    "You are a professional localization engine for Minecraft mods and modpacks.\n"
    "Translate EACH element of the provided JSON array from English to __LANG_NAME__ "
    "(Minecraft locale: __LANG_CODE__).\n"
    "Strict rules:\n"
    " - Do NOT alter placeholders (e.g., %s, %1$s, {count}, {0}).\n"
    " - Do NOT alter namespaced IDs like modid:item or translation keys.\n"
    " - Preserve Minecraft formatting codes (e.g., §a, §b) and JSON structure.\n"
    " - Keep the array length and order EXACTLY the same as input.\n"
    " - Return ONLY a valid JSON array of strings, without any extra commentary.\n"
    "If you are unsure about something, keep it as in the original."
)


def _render_prompt(template: str, target_lang: str) -> str:
    lang_name = _lang_name_from_mc_code(target_lang)
    return template.replace("__LANG_NAME__", lang_name).replace("__LANG_CODE__", target_lang)


def _is_complex_text(t: str, threshold: int) -> bool:
    """
    «Сложная» строка (лор/квесты/чат-JSON/§-коды/длинный текст) — её лучше гнать
    одиночкой и роутить на более сильную модель (STEP 5).
    """
    return (
        len(t) > threshold
        or "\n" in t
        or "{\"text\"" in t
        or "§" in t  # цветовые коды MC
    )


class Translator:
    """
    Переводчик с кэшем, строгой валидацией, ретраями и устойчивой batch-системой.

    Двумерный роутинг строк (STEP 5): {простые/сложные} × {лёгкая/мощная модель}.
      - client (light)  — лёгкая модель: ВСЕ простые строки (даже на мощном
                          железе — не тратим ресурсы зря);
      - complex_client  — мощная/внешняя модель для «сложных» строк (лор, квесты,
                          §-коды, чат-JSON, длинные тексты). Если None → сложные
                          строки уходят на light с разовой пометкой в логе, что
                          качество может быть ниже.
    """

    def __init__(
        self,
        client: LLMClient,
        cache: TranslationCache,
        strict: bool = True,
        complex_client: Optional[LLMClient] = None,
        log: Optional[callable] = None,
    ):
        self.client = client                       # light / simple
        # есть ли ВЫДЕЛЕННЫЙ клиент для сложных строк
        self._has_complex_client = complex_client is not None
        self._complex_client = complex_client or client
        self.cache = cache
        self.strict = strict
        self._log = log or print
        self._warned_complex_fallback = False

        # Параметры ретраев и batch-а
        self.max_attempts = int(getattr(config, "RETRY_MAX_ATTEMPTS", 6))
        self.base_delay   = float(getattr(config, "RETRY_BASE_DELAY", 2.0))
        self.max_delay    = float(getattr(config, "RETRY_MAX_DELAY", 30.0))
        self.jitter       = float(getattr(config, "RETRY_JITTER", 0.25))
        self.cache_fallbacks = bool(getattr(config, "CACHE_FALLBACKS", True))
        self.batch_size   = int(getattr(config, "BATCH_SIZE", 50))

        # Порог "сложной" строки — такие лучше гонять одиночками
        self.complex_len_threshold = int(getattr(config, "COMPLEX_TEXT_THRESHOLD", 220))

        try:
            self.cache.load()
        except Exception:
            pass

    @property
    def hybrid(self) -> bool:
        """True, если сложные строки уходят на отдельный (мощный/внешний) клиент."""
        return self._has_complex_client

    def _pick_client(self, text: str) -> LLMClient:
        """
        Выбор клиента по «сложности» строки (двумерный роутинг, STEP 5):
          - сложная строка → complex_client, если он выделен;
          - иначе (нет выделенного) → light-клиент + разовая пометка в логе.
        Простые строки всегда идут на light-клиент.
        """
        if _is_complex_text(text, self.complex_len_threshold):
            if self._has_complex_client:
                return self._complex_client
            if not self._warned_complex_fallback:
                self._warned_complex_fallback = True
                self._log(
                    "[Routing] Нет выделенной мощной модели для сложных строк — "
                    "используется лёгкая модель, качество лора/квестов может быть ниже. "
                    "Включите tier=standard или hybrid для лучшего качества."
                )
            return self.client
        return self.client

    def flush(self) -> None:
        """Принудительно записать кэш на диск (R-6)."""
        try:
            self.cache.flush()
        except Exception:
            pass

    # ---------- одиночная строка ----------
    def translate(self, text: str, target_lang: str = "ru_ru", client: Optional[LLMClient] = None) -> str:
        """
        Перевод одной строки.
        - пропускаем уже русские строки
        - пропускаем строки без латиницы (только цифры/символы/плейсхолдеры)
        - используем кэш (с учётом целевого языка — R-3)
        - при ошибке сети / лимита — оставляем исходник
        """
        if not text:
            return text

        # уже русский без латиницы → не трогаем
        if _looks_russian_only(text):
            return text

        # нет латиницы вообще — тоже не трогаем (например, чистые числа или id без букв)
        if not _has_latin(text):
            return text

        cached = self.cache.get(text, target_lang)
        if cached is not None:
            return cached

        # если клиент не задан явно — выбираем по сложности строки (STEP 5)
        use_client = client or self._pick_client(text)
        src_tokens = _extract_tokens(text)
        out = self._retry_call(lambda: self._request_single(text, target_lang, use_client))
        if out is None:
            out = text  # при окончательной неудаче — исходник

        # проверка плейсхолдеров
        dst_tokens = _extract_tokens(out)
        if src_tokens != dst_tokens and self.strict:
            out = text

        # пишем в кэш либо только переводы, либо и фоллбеки (в зависимости от флага)
        if out != text or self.cache_fallbacks:
            self.cache.put(text, out, target_lang)
            self.cache.save()
        return out

    # ---------- пачка строк (с дедупом, чанкингом и фоллбеком) ----------
    def translate_many(self, texts: List[str], target_lang: str = "ru_ru") -> List[str]:
        """
        Переводит список строк той же длины.
        Алгоритм:
        - снимаем кэш + отбрасываем уже русские и без-латинские строки;
        - длинные/сложные строки → сразу одиночками (translate);
        - остальные — группируем уникальные и шлём батчами;
        - при проблемах с батчем → фоллбек на одиночный перевод строки.
        """
        n = len(texts)
        results: List[Optional[str]] = [None] * n

        uniq_map: Dict[str, List[int]] = {}
        src_tokens_ref: Dict[str, Tuple[str, ...]] = {}

        for i, t in enumerate(texts):
            if not t:
                results[i] = t
                continue

            # уже русский и без латиницы
            if _looks_russian_only(t) or not _has_latin(t):
                results[i] = t
                continue

            # кэш
            c = self.cache.get(t, target_lang)
            if c is not None:
                results[i] = c
                continue

            # сложные строки — одиночками; translate() сам сроутит их на
            # complex/standard-клиент (или на light с пометкой, если его нет).
            if _is_complex_text(t, self.complex_len_threshold):
                results[i] = self.translate(t, target_lang)
                continue

            # идёт в batch
            if t not in uniq_map:
                uniq_map[t] = []
                src_tokens_ref[t] = _extract_tokens(t)
            uniq_map[t].append(i)

        # если всё уже обработано кэшем/одиночками
        if not uniq_map:
            return [r if r is not None else "" for r in results]

        uniq_texts = list(uniq_map.keys())
        outputs_for_uniq: Dict[str, str] = {}

        for off in range(0, len(uniq_texts), self.batch_size):
            chunk = uniq_texts[off : off + self.batch_size]

            # попытка батч-запроса с ретраями (массовые строки → основной client)
            try:
                translated_chunk = self._retry_call(
                    lambda: self._request_batch(chunk, target_lang, self.client)
                )
            except Exception as e:
                print(f"[BatchFatal] {e} → fallback to single for this chunk")
                translated_chunk = None

            # если батч сломался — одиночками
            if translated_chunk is None or len(translated_chunk) != len(chunk):
                translated_chunk = [self.translate(t, target_lang) for t in chunk]

            # валидация плейсхолдеров + кэш
            for src, out in zip(chunk, translated_chunk):
                want = src_tokens_ref[src]
                got  = _extract_tokens(out)
                if want != got and self.strict:
                    out = src
                outputs_for_uniq[src] = out
                if out != src or self.cache_fallbacks:
                    self.cache.put(src, out, target_lang)

            # периодически сохраняем кэш (дебаунс внутри cache.save)
            self.cache.save()

        # разбрасываем по исходным индексам
        for src, positions in uniq_map.items():
            out = outputs_for_uniq.get(src, src)
            for i in positions:
                results[i] = out

        return [r if r is not None else "" for r in results]

    # ---------- механизм ретраев ----------
    def _retry_call(self, func):
        attempts = 0
        last_err: Optional[Exception] = None
        while attempts < self.max_attempts:
            attempts += 1
            try:
                return func()
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                is_rate = (
                    isinstance(e, RateLimitError)
                    or ("429" in msg)
                    or ("too many requests" in msg)
                    or ("rate limit" in msg)
                )
                is_net = isinstance(e, (urllib.error.URLError, TimeoutError))
                if not (is_rate or is_net):
                    # логическая/парсинговая ошибка — не мучаемся ретраями
                    break
                delay = min(self.max_delay, self.base_delay * (2 ** (attempts - 1)))
                delay *= 1.0 + random.uniform(-self.jitter, self.jitter)
                delay = max(0.0, delay)
                print(f"[Retry] attempt {attempts}/{self.max_attempts}, sleep {delay:.1f}s, err={e}")
                time.sleep(delay)
        if last_err:
            print(f"[TranslateError] giving up after {attempts} attempts: {last_err}")
        return None

    # ---------- построение промптов (из модульных констант — STEP 4) ----------
    @staticmethod
    def _single_system_prompt(target_lang: str) -> str:
        return _render_prompt(SINGLE_SYSTEM_PROMPT_TEMPLATE, target_lang)

    @staticmethod
    def _batch_system_prompt(target_lang: str) -> str:
        return _render_prompt(BATCH_SYSTEM_PROMPT_TEMPLATE, target_lang)

    # ---------- вызовы LLM через клиент ----------
    def _request_single(self, text: str, target_lang: str, client: LLMClient) -> str:
        messages = [
            {"role": "system", "content": self._single_system_prompt(target_lang)},
            {"role": "user", "content": text},
        ]
        out = client.chat(messages, temperature=0, timeout=60)
        return out.strip()

    def _request_batch(self, texts: List[str], target_lang: str, client: LLMClient) -> List[str]:
        user_content = json.dumps(texts, ensure_ascii=False)
        messages = [
            {"role": "system", "content": self._batch_system_prompt(target_lang)},
            {"role": "user", "content": user_content},
        ]
        raw = client.chat(messages, temperature=0, timeout=90)
        return _coerce_json_array(raw.strip(), expected_len=len(texts))
