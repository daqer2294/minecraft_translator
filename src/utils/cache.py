# src/utils/cache.py
from __future__ import annotations

import atexit
import json
import os
import threading
import time
from typing import Dict, Optional


class TranslationCache:
    """
    Файловая память переводов.

    Хранит переводы с разбивкой по целевому языку:
        { "ru_ru": { "Iron Ingot": "Железный слиток", ... },
          "de_de": { "Iron Ingot": "Eisenbarren", ... } }

    R-3: ключ кэша теперь зависит от целевого языка — переводы для ru_ru
         больше не «протекают» в de_de и наоборот.
    R-6: save() дебаунсится (не переписывает файл после каждой строки);
         реальная запись раз в `save_interval` секунд ИЛИ раз в `save_every`
         изменений. Финальная запись гарантируется flush() и atexit.
    """

    # язык по умолчанию для миграции старого «плоского» кэша
    _DEFAULT_LANG = "ru_ru"

    def __init__(
        self,
        path: str,
        save_interval: float = 3.0,
        save_every: int = 200,
    ):
        self.path = path
        # lang -> { src -> dst }
        self._data: Dict[str, Dict[str, str]] = {}
        self._loaded = False

        # thread-safety: mirrorer гоняет несколько потоков на один кэш
        self._lock = threading.RLock()

        # дебаунс сохранения
        self._save_interval = float(save_interval)
        self._save_every = int(save_every)
        self._dirty = 0
        self._last_save = 0.0

        # финальный флеш на выходе процесса
        atexit.register(self.flush)

    # ---- базовые операции ----
    def load(self) -> None:
        """Загрузка кэша с диска (один раз). Мигрирует старый плоский формат."""
        with self._lock:
            if self._loaded:
                return
            if self.path and os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    self._data = self._normalize_loaded(raw)
                except Exception:
                    self._data = {}
            self._loaded = True

    def _normalize_loaded(self, raw) -> Dict[str, Dict[str, str]]:
        """
        Приводим содержимое файла к формату {lang: {src: dst}}.

        - старый формат {src: dst} (значения — строки) → кладём под _DEFAULT_LANG;
        - новый формат {lang: {src: dst}} → используем как есть.
        """
        if not isinstance(raw, dict) or not raw:
            return {}

        # если хотя бы одно значение — dict, считаем формат уже вложенным
        looks_nested = any(isinstance(v, dict) for v in raw.values())
        if looks_nested:
            out: Dict[str, Dict[str, str]] = {}
            for lang, mapping in raw.items():
                if isinstance(mapping, dict):
                    out[str(lang)] = {str(k): str(v) for k, v in mapping.items()}
            return out

        # старый плоский формат → миграция под язык по умолчанию
        default_lang = getattr(_config(), "TARGET_LANG", self._DEFAULT_LANG)
        return {default_lang: {str(k): str(v) for k, v in raw.items()}}

    def _write(self) -> None:
        """Физическая запись на диск (под замком)."""
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)  # атомарная замена
        self._dirty = 0
        self._last_save = time.time()

    def save(self, force: bool = False) -> None:
        """
        Дебаунс-сохранение (R-6).
        Реально пишет, только если накопились изменения и:
          - force=True, ИЛИ
          - прошло >= save_interval секунд, ИЛИ
          - накопилось >= save_every изменений.
        """
        with self._lock:
            if self._dirty == 0 and not force:
                return
            now = time.time()
            due = (
                force
                or (now - self._last_save) >= self._save_interval
                or self._dirty >= self._save_every
            )
            if due:
                self._write()

    def flush(self) -> None:
        """Принудительно записать всё, что накопилось."""
        with self._lock:
            if self._dirty > 0:
                self._write()

    # ---- интерфейс для работы ----
    def get(self, src: str, lang: str = _DEFAULT_LANG) -> Optional[str]:
        """Вернуть перевод по (исходная строка, целевой язык) или None."""
        with self._lock:
            return self._data.get(lang, {}).get(src)

    def put(self, src: str, dst: str, lang: str = _DEFAULT_LANG) -> None:
        """Сохранить перевод в память (в намспейс целевого языка)."""
        with self._lock:
            self._data.setdefault(lang, {})[src] = dst
            self._dirty += 1

    def __len__(self) -> int:
        with self._lock:
            return sum(len(m) for m in self._data.values())

    # ---- удобные свойства ----
    @property
    def data(self) -> Dict[str, Dict[str, str]]:
        return self._data


def _config():
    """Ленивая ссылка на config, чтобы избежать циклических импортов."""
    from .. import config
    return config
