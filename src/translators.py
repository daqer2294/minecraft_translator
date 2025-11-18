# src/translators.py
from __future__ import annotations

import json
import os
import re
import time
import random
import ssl
from typing import Optional, List, Tuple, Dict

import urllib.request
import urllib.error
import certifi

from .utils.cache import TranslationCache
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


class Translator:
    """
    Переводчик с кэшем, строгой валидацией, ретраями и устойчивая batch-системой.
    """

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: Optional[str],
        cache: TranslationCache,
        strict: bool = True,
    ):
        self.provider = provider
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or getattr(config, "OPENAI_API_KEY", "")
        self.cache = cache
        self.strict = strict

        # Параметры ретраев и batch-а
        self.max_attempts = int(getattr(config, "RETRY_MAX_ATTEMPTS", 6))
        self.base_delay   = float(getattr(config, "RETRY_BASE_DELAY", 2.0))
        self.max_delay    = float(getattr(config, "RETRY_MAX_DELAY", 30.0))
        self.jitter       = float(getattr(config, "RETRY_JITTER", 0.25))
        self.cache_fallbacks = bool(getattr(config, "CACHE_FALLBACKS", True))
        self.batch_size   = int(getattr(config, "BATCH_SIZE", 50))

        # Порог "сложной" строки — такие лучше гонять одиночками
        self.complex_len_threshold = int(getattr(config, "COMPLEX_TEXT_THRESHOLD", 220))

        self.base_url = getattr(config, "OPENAI_BASE_URL", "https://api.openai.com")
        self._ssl_ctx = ssl.create_default_context(cafile=certifi.where())

        try:
            self.cache.load()
        except Exception:
            pass

    # ---------- одиночная строка ----------
    def translate(self, text: str, target_lang: str = "ru_ru") -> str:
        """
        Перевод одной строки.
        - пропускаем уже русские строки
        - пропускаем строки без латиницы (только цифры/символы/плейсхолдеры)
        - используем кэш
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

        cached = self.cache.get(text)
        if cached is not None:
            return cached

        src_tokens = _extract_tokens(text)
        out = self._retry_call(lambda: self._request_single(text, target_lang))
        if out is None:
            out = text  # при окончательной неудаче — исходник

        # проверка плейсхолдеров
        dst_tokens = _extract_tokens(out)
        if src_tokens != dst_tokens and self.strict:
            out = text

        # пишем в кэш либо только переводы, либо и фоллбеки (в зависимости от флага)
        if out != text or self.cache_fallbacks:
            self.cache.put(text, out)
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
            c = self.cache.get(t)
            if c is not None:
                results[i] = c
                continue

            # сложные строки — одиночками:
            # - очень длинные
            # - с переносами строк
            # - с SNBT/JSON кусками
            if (
                len(t) > self.complex_len_threshold
                or "\n" in t
                or "{\"text\"" in t
                or "§" in t  # цветовые коды MC
            ):
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

            # попытка батч-запроса с ретраями
            try:
                translated_chunk = self._retry_call(lambda: self._request_batch(chunk, target_lang))
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
                    self.cache.put(src, out)

            # периодически сохраняем кэш
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
                is_rate = ("429" in msg) or ("too many requests" in msg) or ("rate limit" in msg)
                is_net  = isinstance(e, (urllib.error.URLError, TimeoutError))
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

    # ---------- HTTP: одиночный запрос ----------
    def _request_single(self, text: str, target_lang: str) -> str:
        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        lang_name = _lang_name_from_mc_code(target_lang)
        sys_prompt = (
            "You are a professional localization engine for Minecraft mods and modpacks.\n"
            f"Translate the following text into {lang_name} (Minecraft locale: {target_lang}).\n"
            "Hard rules:\n"
            " - Do NOT translate or alter placeholders (e.g., %s, %1$s, {count}, {0}).\n"
            " - Do NOT translate or alter namespaced IDs like modid:item or text keys.\n"
            " - Do NOT leave parts of the text in English unless they are proper names or IDs.\n"
            " - Keep formatting codes (§a, §b, etc.) and JSON fragments intact.\n"
            "Return ONLY the translated text, without quotes or explanations."
        )
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": text},
            ],
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60, context=self._ssl_ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()

    # ---------- HTTP: batch-запрос ----------
    def _request_batch(self, texts: List[str], target_lang: str) -> List[str]:
        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        lang_name = _lang_name_from_mc_code(target_lang)
        sys_prompt = (
            "You are a professional localization engine for Minecraft mods and modpacks.\n"
            f"Translate EACH element of the provided JSON array from English to {lang_name} "
            f"(Minecraft locale: {target_lang}).\n"
            "Strict rules:\n"
            " - Do NOT alter placeholders (e.g., %s, %1$s, {count}, {0}).\n"
            " - Do NOT alter namespaced IDs like modid:item or translation keys.\n"
            " - Preserve Minecraft formatting codes (e.g., §a, §b) and JSON structure.\n"
            " - Keep the array length and order EXACTLY the same as input.\n"
            " - Return ONLY a valid JSON array of strings, without any extra commentary.\n"
            "If you are unsure about something, keep it as in the original."
        )
        user_content = json.dumps(texts, ensure_ascii=False)
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90, context=self._ssl_ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        raw = data["choices"][0]["message"]["content"].strip()
        arr = _coerce_json_array(raw, expected_len=len(texts))
        return arr
