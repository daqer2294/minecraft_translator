# src/translators.py
from __future__ import annotations

import json
import os
import re
import time
import random
import ssl
from typing import Optional, List, Tuple, Dict, Callable

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
    if not s:
        return tuple()
    toks = []
    toks.extend(RE_PLACEHOLDER.findall(s))
    toks.extend(RE_NAMESPACE.findall(s))
    return tuple(sorted(toks))


def _looks_russian(s: str) -> bool:
    # Строка явно русская и без латиницы → не переводим
    return bool(RE_CYR.search(s)) and not RE_LATIN.search(s)


# ---------- резка длинных строк по предложениям ----------
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?…])\s+')

def _max_chunk_len() -> int:
    return int(getattr(config, "MAX_CHUNK_LEN", 100))


def _split_text_for_translation(text: str, max_len: int | None = None) -> List[str]:
    """
    Делит текст на куски <= max_len, стараясь резать по предложениям.
    Если одно предложение > max_len — режем грубо по символам.
    """
    if max_len is None:
        max_len = _max_chunk_len()

    text = text.strip()
    if not text or len(text) <= max_len:
        return [text]

    sentences = _SENTENCE_SPLIT_RE.split(text)

    chunks: List[str] = []
    cur = ""

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue

        candidate = sent if not cur else (cur + " " + sent)

        if len(candidate) <= max_len:
            cur = candidate
        else:
            if cur:
                chunks.append(cur)
            if len(sent) <= max_len:
                cur = sent
            else:
                # одно предложение само по себе длинное → режем по max_len
                for i in range(0, len(sent), max_len):
                    chunks.append(sent[i:i + max_len])
                cur = ""

    if cur:
        chunks.append(cur)

    return chunks


def _lang_name(code: str) -> str:
    """
    Возвращает человекочитаемое имя языка по MC-коду (ru_ru -> Russian),
    если в config есть MC_LANG_NAMES.
    """
    mapping = getattr(config, "MC_LANG_NAMES", None)
    if isinstance(mapping, dict):
        return mapping.get(code.lower(), code)
    return code


class Translator:
    """
    Переводчик с кэшем, строгой валидацией, ретраями и batch-потоком.
    Работает с:
      - translate(text)
      - translate_many([texts...])
    Внутри:
      - режет длинные строки на куски по предложениям (<= MAX_CHUNK_LEN)
      - батчит запросы до config.BATCH_SIZE
      - кэширует все результаты (включая fallback-английский, если strict отклонил перевод)
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

        self.max_attempts = int(getattr(config, "RETRY_MAX_ATTEMPTS", 6))
        self.base_delay   = float(getattr(config, "RETRY_BASE_DELAY", 2.0))
        self.max_delay    = float(getattr(config, "RETRY_MAX_DELAY", 30.0))
        self.jitter       = float(getattr(config, "RETRY_JITTER", 0.25))
        self.cache_fallbacks = bool(getattr(config, "CACHE_FALLBACKS", True))
        self.batch_size   = int(getattr(config, "BATCH_SIZE", 50))

        self.base_url = getattr(config, "OPENAI_BASE_URL", "https://api.openai.com")
        self._ssl_ctx = ssl.create_default_context(cafile=certifi.where())

        try:
            self.cache.load()
        except Exception:
            pass

    # ---------- публичные методы ----------

    def translate(self, text: str, target_lang: str = "ru_ru") -> str:
        """
        Переводит одну строку.
        Внутри просто вызывает translate_many([text])[0],
        чтобы вся логика кэша/резки/батчей была в одном месте.
        """
        if not text or _looks_russian(text):
            return text
        return self.translate_many([text], target_lang)[0]

    def translate_many(self, texts: List[str], target_lang: str = "ru_ru") -> List[str]:
        """
        Переводит список строк:
          - уже русские/пустые → возвращает как есть;
          - остальные режет на куски по предложениям;
          - все куски прогоняет через _translate_flat (кэш + батчи);
          - собирает куски обратно в длинные строки.
        """
        if not texts:
            return []

        max_len = _max_chunk_len()

        # результирующий список (заполним по ходу)
        results: List[Optional[str]] = [None] * len(texts)

        # Маппинг: для каждой исходной строки — сколько кусочков
        chunks_per_text: List[int] = []
        flat_chunks: List[str] = []

        for i, t in enumerate(texts):
            if not t or _looks_russian(t):
                results[i] = t
                chunks_per_text.append(0)
                continue

            # сразу попробуем кэш
            cached = self.cache.get(t)
            if cached is not None:
                results[i] = cached
                chunks_per_text.append(0)
                continue

            # нужно реально переводить → режем
            parts = _split_text_for_translation(t, max_len=max_len)
            chunks_per_text.append(len(parts))
            flat_chunks.extend(parts)

        # Если нечего переводить — просто вернём уже заполненные значения
        if not flat_chunks:
            return [r if r is not None else "" for r in results]

        # Переводим куски (каждый кусок короткий)
        translated_chunks = self._translate_flat(flat_chunks, target_lang)

        # Собираем куски обратно
        idx = 0
        for i, cnt in enumerate(chunks_per_text):
            if cnt == 0:
                # либо была русская/пустая строка, либо кэш → уже стоит в results[i]
                continue
            piece_list = translated_chunks[idx: idx + cnt]
            idx += cnt
            joined = " ".join(piece_list)
            results[i] = joined
            # кладём в кэш как целую строку
            if joined != texts[i] or self.cache_fallbacks:
                self.cache.put(texts[i], joined)

        # финально сохраняем кэш
        try:
            self.cache.save()
        except Exception:
            pass

        return [r if r is not None else "" for r in results]

    # ---------- внутренний batch-поток для коротких строк ----------

    def _translate_flat(self, texts: List[str], target_lang: str) -> List[str]:
        """
        Переводит список КОРОТКИХ строк (после резки), применяя:
          - кэш
          - дедуп
          - batch-запросы к API
        Возвращает список той же длины.
        """
        n = len(texts)
        results: List[Optional[str]] = [None] * n

        uniq_map: Dict[str, List[int]] = {}
        src_tokens_ref: Dict[str, Tuple[str, ...]] = {}

        # Сначала снимаем кэш / русские строки
        for i, t in enumerate(texts):
            if not t or _looks_russian(t):
                results[i] = t
                continue
            c = self.cache.get(t)
            if c is not None:
                results[i] = c
                continue

            if t not in uniq_map:
                uniq_map[t] = []
                src_tokens_ref[t] = _extract_tokens(t)
            uniq_map[t].append(i)

        if not uniq_map:
            return [r if r is not None else "" for r in results]

        uniq_texts = list(uniq_map.keys())
        outputs_for_uniq: Dict[str, str] = {}

        # Обрабатываем уникальные строки батчами
        for offset in range(0, len(uniq_texts), self.batch_size):
            chunk = uniq_texts[offset: offset + self.batch_size]

            # Запрашиваем перевод chunk с ретраями
            translated_chunk = self._retry_call(lambda: self._request_batch(chunk, target_lang))

            if translated_chunk is None or len(translated_chunk) != len(chunk):
                # что-то пошло не так — fallback поодиночке
                translated_chunk = []
                for src in chunk:
                    single = self._retry_call(lambda: self._request_single(src, target_lang))
                    if single is None:
                        single = src
                    translated_chunk.append(single)

            # Валидация плейсхолдеров + кэш
            for src, out in zip(chunk, translated_chunk):
                want = src_tokens_ref[src]
                got = _extract_tokens(out)
                if want != got and self.strict:
                    out = src

                outputs_for_uniq[src] = out
                if out != src or self.cache_fallbacks:
                    self.cache.put(src, out)

            try:
                self.cache.save()
            except Exception:
                pass

        # Раскладываем по позициям
        for src, positions in uniq_map.items():
            out = outputs_for_uniq.get(src, src)
            for idx in positions:
                results[idx] = out

        return [r if r is not None else "" for r in results]

    # ---------- ретраи ----------

    def _retry_call(self, func: Callable[[], str | List[str] | None]) -> str | List[str] | None:
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
                is_net = isinstance(e, (urllib.error.URLError, TimeoutError))
                if not (is_rate or is_net):
                    break

                delay = min(self.max_delay, self.base_delay * (2 ** (attempts - 1)))
                delay *= 1.0 + random.uniform(-self.jitter, self.jitter)
                time.sleep(max(0.0, delay))

        if last_err:
            print(f"[TranslateError] giving up after {self.max_attempts} attempts: {last_err}")
        return None

    # ---------- HTTP-часть ----------

    def _request_single(self, text: str, target_lang: str) -> str:
        """
        Перевод одной строки через /v1/chat/completions.
        Используется только как fallback, основной поток — _request_batch.
        """
        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        lang_name = _lang_name(target_lang)
        sys_prompt = (
            "You are a professional localization engine for Minecraft mods and modpacks.\n"
            f"Translate the following English text into {lang_name} (Minecraft locale: {target_lang}).\n"
            "Very important rules:\n"
            " - Preserve ALL Minecraft color/format codes (like §0–§f, §l, §n, §r).\n"
            " - Preserve ALL placeholders (e.g., %s, %1$s, {count}, {0}, {player}).\n"
            " - Do NOT translate namespaced IDs like modid:item, minecraft:stone, ftbquests:lootcrate.\n"
            " - Keep newlines and escape sequences (\\n, \\t, \\\").\n"
            " - Style: natural, game-like, not overly formal.\n"
            "Output: ONLY the translated text, nothing else."
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
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=60, context=self._ssl_ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()

    def _request_batch(self, texts: List[str], target_lang: str) -> List[str]:
        """
        Переводит массив строк. Модель должна вернуть JSON-массив той же длины.
        """
        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        lang_name = _lang_name(target_lang)
        sys_prompt = (
            "You are a professional localization engine for Minecraft mods and modpacks.\n"
            f"Translate EACH element of the provided JSON array from English into {lang_name} "
            f"(Minecraft locale: {target_lang}).\n"
            "Strict rules:\n"
            " - Preserve ALL Minecraft color/format codes (like §0–§f, §l, §n, §r).\n"
            " - Preserve ALL placeholders (e.g., %s, %1$s, {count}, {0}, {player}).\n"
            " - Do NOT translate namespaced IDs like modid:item, minecraft:stone, ftbquests:lootcrate.\n"
            " - Keep array length and order EXACTLY the same.\n"
            " - Output MUST be a valid JSON array of strings, no extra explanations."
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
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=90, context=self._ssl_ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        raw = data["choices"][0]["message"]["content"].strip()
        # на случай, если модель зачем-то обернёт в ```json ... ```
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.lstrip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.rstrip("`").strip()

        arr = json.loads(raw)
        if not isinstance(arr, list) or len(arr) != len(texts):
            raise RuntimeError("Batch output format mismatch")
        return [str(x) for x in arr]
