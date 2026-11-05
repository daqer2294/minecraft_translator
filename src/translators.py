# src/translators.py
from __future__ import annotations

import json
import re
import threading
import time
import random
import urllib.error
from typing import Optional, List, Tuple, Dict

from .utils.cache import TranslationCache
from .utils.helpers import is_untranslatable
from .utils.markup import (
    MARKUP_RE,
    MASK_PROMPT_NOTE,
    MASK_PROMPT_NOTE_RU,
    answer_without_cyrillic,
    foreign_latin_words,
    has_intro_or_raw_mask,
    has_mixed_script_word,
    invented_tags,
    looks_hallucinated,
    mask,
    protected_code_changed,
    strip_newline_dots,
    unmask,
)
from .utils.prompt_leak import PromptLeakDetector
from .llm.base import LLMClient, RateLimitError, LLMTimeoutError, LLMCancelledError, LLMTruncatedError
from . import config

# -------- защитные регекспы --------
RE_LATIN = re.compile(r"[A-Za-z]")
RE_CYR   = re.compile(r"[А-Яа-яЁё]")
# ID объекта FTB Quests ("18E5446B92E771B8") или ссылка на него ("#18E5446B92E771B8")
RE_BARE_ID = re.compile(r"#?[0-9A-Fa-f]{16}")


# Лимит ответа. Перевод занимает около 0,5 токена на символ источника, запас почти
# тройной. Без лимита зациклившаяся модель пишет до конца контекста: минуты на строку.
_MIN_OUTPUT_TOKENS = 128
_MIN_BATCH_ITEM_TOKENS = 32
_BATCH_OVERHEAD_TOKENS = 64


def _single_token_cap(text: str) -> int:
    return max(_MIN_OUTPUT_TOKENS, int(len(text) * 1.5))


def _batch_token_cap(texts: List[str]) -> int:
    return sum(max(_MIN_BATCH_ITEM_TOKENS, int(len(t) * 1.5)) for t in texts) + _BATCH_OVERHEAD_TOKENS


# Иероглифы, кана и хангыль. Модель иногда вставляет их в чужой язык: «Необходимо至少».
RE_CJK = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")
_CJK_LANGUAGES = ("zh", "ja", "ko", "lzh")


def _has_foreign_cjk(text: str, target_lang: str) -> bool:
    """В ответе есть CJK-символы, а целевой язык не китайский, японский или корейский."""
    if target_lang.lower().split("_")[0] in _CJK_LANGUAGES:
        return False
    return RE_CJK.search(text) is not None


def _is_bare_id(s: str) -> bool:
    return RE_BARE_ID.fullmatch(s.strip()) is not None


def _extract_tokens(s: str) -> Tuple[str, ...]:
    """Вся разметка строки: она должна совпасть в источнике и переводе, порядок не важен."""
    if not s:
        return tuple()
    return tuple(sorted(MARKUP_RE.findall(s)))


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


# Все тексты, которые модель может пересказать вместо перевода. Новый промпт добавляется сюда.
PROMPT_TEXTS = (
    SINGLE_SYSTEM_PROMPT_TEMPLATE,
    BATCH_SYSTEM_PROMPT_TEMPLATE,
    MASK_PROMPT_NOTE,
    MASK_PROMPT_NOTE_RU,
)
PROMPT_LEAK = PromptLeakDetector(PROMPT_TEXTS)


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
        cancel_event: Optional[threading.Event] = None,
        target_lang: Optional[str] = None,
        before_request: Optional[callable] = None,
    ):
        # P0-3: язык фиксируется ОДИН раз на весь прогон. Воркеры больше не читают
        # изменяемый глобал config.TARGET_LANG — смена языка в UI во время перевода
        # не может «расщепить» вывод на два языка.
        self.target_lang = target_lang or getattr(config, "TARGET_LANG", "ru_ru")
        self.client = client                       # light / simple
        # есть ли ВЫДЕЛЕННЫЙ клиент для сложных строк
        self._has_complex_client = complex_client is not None
        self._complex_client = complex_client or client
        self.cache = cache
        self.strict = strict
        self._log = log or print
        self._warned_complex_fallback = False
        # Ошибка последнего запроса своя у каждого потока: воркеры не мешают друг другу.
        self._thread_state = threading.local()
        # Строки, где модель ответила браком: их переводит второй проход.
        self._rejected: Dict[Tuple[str, str], None] = {}
        self._rejected_lock = threading.Lock()
        # Только кэш, без запросов: для пересборки вывода после второго прохода.
        self.cache_only = False
        # Событие отмены: прокидывается в client.chat() и рвёт зависший HTTP-запрос
        # к модели по нажатию «Стоп» (а не ждёт границы файла).
        self.cancel_event = cancel_event
        # Вызывается перед каждым запросом к модели: здесь держится пауза.
        self._before_request = before_request
        self._warmup_logged = False
        # Таймауты запроса к модели. Первый запрос к Ollama/локальному серверу
        # может грузить модель в память (десятки секунд), поэтому щедрые значения.
        self.single_timeout = float(getattr(config, "LLM_SINGLE_TIMEOUT", 120.0))
        self.batch_timeout  = float(getattr(config, "LLM_BATCH_TIMEOUT", 180.0))

        # Параметры ретраев и batch-а
        self.max_attempts = int(getattr(config, "RETRY_MAX_ATTEMPTS", 6))
        self.base_delay   = float(getattr(config, "RETRY_BASE_DELAY", 2.0))
        self.max_delay    = float(getattr(config, "RETRY_MAX_DELAY", 30.0))
        self.jitter       = float(getattr(config, "RETRY_JITTER", 0.25))
        self.batch_size   = int(getattr(config, "BATCH_SIZE", 50))

        # Порог "сложной" строки — такие лучше гонять одиночками
        self.complex_len_threshold = int(getattr(config, "COMPLEX_TEXT_THRESHOLD", 220))

        try:
            self.cache.load()
        except Exception:
            pass

    @property
    def _last_error(self) -> Optional[Exception]:
        return getattr(self._thread_state, "last_error", None)

    @_last_error.setter
    def _last_error(self, error: Optional[Exception]) -> None:
        self._thread_state.last_error = error

    def take_rejected(self) -> List[Tuple[str, str]]:
        """Отдаёт накопленные (строка, язык) с браком модели и очищает список."""
        with self._rejected_lock:
            items = list(self._rejected)
            self._rejected.clear()
        return items

    def _note_rejected(self, text: str, target_lang: str) -> None:
        with self._rejected_lock:
            self._rejected[(text, target_lang)] = None

    def _is_model_fault(self, out: Optional[str]) -> bool:
        """Брак самой модели. Обрыв связи, таймаут и «Стоп» сюда не входят."""
        if out:
            return True
        if self._cancelled():
            return False
        error = self._last_error
        return error is None or isinstance(error, LLMTruncatedError)

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

    def _cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    def _warmup_note(self) -> None:
        """Одноразовая подсказка перед самым первым запросом к модели: холодный
        старт (загрузка модели в память) может занять до нескольких минут."""
        if not self._warmup_logged:
            self._warmup_logged = True
            self._log(
                "⏳ Первый запрос к модели: если она ещё не загружена в память, "
                "это может занять до нескольких минут…"
            )

    # ---------- одиночная строка ----------
    def translate(self, text: str, target_lang: Optional[str] = None, client: Optional[LLMClient] = None) -> str:
        """
        Перевод одной строки.
        - пропускаем уже русские строки
        - пропускаем строки без латиницы (только цифры/символы/плейсхолдеры)
        - используем кэш (с учётом целевого языка — R-3)
        - при ошибке сети / лимита — оставляем исходник

        target_lang=None → снимок языка, зафиксированный на старте прогона (P0-3).
        """
        target_lang = target_lang or self.target_lang
        if not text:
            return text

        # отмена запрошена — не начинаем новых запросов, оставляем исходник
        if self._cancelled():
            return text

        # уже русский без латиницы → не трогаем
        if _looks_russian_only(text):
            return text

        # нет латиницы вообще — тоже не трогаем (например, чистые числа или id без букв)
        if not _has_latin(text):
            return text

        # ID и прочее без слов. Проверка стоит до кэша, чтобы старая запись их не подменила.
        if _is_bare_id(text) or is_untranslatable(text):
            return text

        cached = self.cache.get(text, target_lang)
        if cached is not None:
            return cached
        if self.cache_only:
            return text

        # если клиент не задан явно — выбираем по сложности строки (STEP 5)
        use_client = client or self._pick_client(text)
        src_tokens = _extract_tokens(text)
        out = strip_newline_dots(text, self._translate_masked(text, target_lang, use_client))
        # Сбой, отмена, пустой ответ или брак: отдаём исходник
        if self._answer_rejected(out, src_tokens, target_lang, text):
            if self._is_model_fault(out):
                self._note_rejected(text, target_lang)
            return text

        # В кэш идёт только настоящий перевод, иначе английский останется навсегда:
        # следующий прогон со здоровой моделью возьмёт его из кэша без запроса.
        if out != text:
            self.cache.put(text, out, target_lang)
            self.cache.save()
        return out

    # ---------- пачка строк (с дедупом, чанкингом и фоллбеком) ----------
    def translate_many(self, texts: List[str], target_lang: Optional[str] = None) -> List[str]:
        """
        Переводит список строк той же длины.
        target_lang=None → снимок языка прогона (P0-3).
        Алгоритм:
        - снимаем кэш + отбрасываем уже русские и без-латинские строки;
        - длинные/сложные строки → сразу одиночками (translate);
        - остальные — группируем уникальные и шлём батчами;
        - при проблемах с батчем → фоллбек на одиночный перевод строки.
        """
        target_lang = target_lang or self.target_lang
        n = len(texts)
        results: List[Optional[str]] = [None] * n

        uniq_map: Dict[str, List[int]] = {}
        src_tokens_ref: Dict[str, Tuple[str, ...]] = {}

        for i, t in enumerate(texts):
            if not t:
                results[i] = t
                continue

            # уже русский и без латиницы
            if _looks_russian_only(t) or not _has_latin(t) or _is_bare_id(t) or is_untranslatable(t):
                results[i] = t
                continue

            # кэш
            c = self.cache.get(t, target_lang)
            if c is not None:
                results[i] = c
                continue
            if self.cache_only:
                results[i] = t
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
            # отмена во время многострочного перевода — прекращаем, остальные
            # строки останутся исходными (файл быстро «завершится» → stop сработает)
            if self._cancelled():
                break
            chunk = uniq_texts[off : off + self.batch_size]

            # попытка батч-запроса с ретраями (массовые строки → основной client)
            try:
                translated_chunk = self._retry_call(
                    lambda: self._request_batch(chunk, target_lang, self.client)
                )
            except Exception as e:
                print(f"[BatchFatal] {e} → fallback to single for this chunk")
                translated_chunk = None

            # отмена во время батча — оставляем исходники, дальше не идём
            if self._cancelled():
                for t in chunk:
                    outputs_for_uniq.setdefault(t, t)
                break

            # если батч сломался — одиночками, НО не при таймауте: молчащий сервер
            # иначе дал бы шторм одиночных ретраев (по timeout на каждую строку).
            if translated_chunk is None or len(translated_chunk) != len(chunk):
                if isinstance(self._last_error, (LLMTimeoutError, LLMCancelledError)):
                    self._log(
                        "⏱ Модель не ответила вовремя — строки этого блока оставлены "
                        "без перевода (без шторма одиночных запросов)."
                    )
                    translated_chunk = list(chunk)  # исходники
                else:
                    translated_chunk = [self.translate(t, target_lang) for t in chunk]

            # валидация плейсхолдеров + кэш
            for src, out in zip(chunk, translated_chunk):
                # Модель потеряла токен разметки: эту строку спрашиваем отдельно
                if out is None:
                    out = self.translate(src, target_lang)
                out = strip_newline_dots(src, out)
                if self._answer_rejected(out, src_tokens_ref[src], target_lang, src):
                    if out != src:
                        self._note_rejected(src, target_lang)
                    out = src
                outputs_for_uniq[src] = out
                if out != src:
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
    def _answer_rejected(self, out: Optional[str], src_tokens: Tuple[str, ...], target_lang: str, source: str) -> bool:
        """Такой ответ нельзя показывать и кэшировать: в следующий прогон строка спросится снова."""
        return (
            not out
            or _has_foreign_cjk(out, target_lang)
            or has_mixed_script_word(out)
            or bool(PROMPT_LEAK.explain(out, source))
            or looks_hallucinated(source, out)
            or has_intro_or_raw_mask(source, out)
            or bool(foreign_latin_words(source, out))
            or protected_code_changed(source, out)
            or bool(invented_tags(source, out))
            or answer_without_cyrillic(source, out, target_lang)
            or (self.strict and src_tokens != _extract_tokens(out))
        )

    def _translate_masked(self, text: str, target_lang: str, client: LLMClient) -> Optional[str]:
        """Перевод без разметки в запросе. Если модель потеряла токен, ещё раз целиком."""
        masked = mask(text)
        answer = self._retry_call(
            lambda: self._request_single(masked.core, target_lang, client, bool(masked.tokens))
        )
        if not answer:
            return None
        restored = unmask(answer, masked)
        if restored is not None:
            return restored
        return self._retry_call(lambda: self._request_single(text, target_lang, client))

    def _retry_call(self, func):
        attempts = 0
        last_err: Optional[Exception] = None
        self._last_error = None
        while attempts < self.max_attempts:
            if self._cancelled():
                break
            attempts += 1
            try:
                return func()
            except Exception as e:
                last_err = e
                # отмена пользователем — не ретраим, выходим немедленно
                if self._cancelled():
                    break
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
            self._last_error = last_err
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
    def _request_single(self, text: str, target_lang: str, client: LLMClient, masked: bool = False) -> str:
        # Пояснение о токенах только там, где они есть: иначе модель выдумывает их сама
        # («Leaves» → «[[0]] Leafs [[1]]») и чаще оставляет строку непереведённой.
        system = self._single_system_prompt(target_lang) + (MASK_PROMPT_NOTE if masked else "")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ]
        self._warmup_note()
        if self._before_request:
            self._before_request()
        out = client.chat(
            messages, temperature=0, max_tokens=_single_token_cap(text),
            timeout=self.single_timeout, cancel_event=self.cancel_event,
        )
        return out.strip()

    def _request_batch(self, texts: List[str], target_lang: str, client: LLMClient) -> List[Optional[str]]:
        """Элемент None: модель потеряла токен разметки в этой строке."""
        masked = [mask(t) for t in texts]
        cores = [m.core for m in masked]
        user_content = json.dumps(cores, ensure_ascii=False)
        system = self._batch_system_prompt(target_lang)
        if any(m.tokens for m in masked):
            system += MASK_PROMPT_NOTE
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        self._warmup_note()
        if self._before_request:
            self._before_request()
        raw = client.chat(
            messages, temperature=0, max_tokens=_batch_token_cap(cores),
            timeout=self.batch_timeout, cancel_event=self.cancel_event,
        )
        answers = _coerce_json_array(raw.strip(), expected_len=len(texts))
        return [unmask(a, m) for a, m in zip(answers, masked)]
