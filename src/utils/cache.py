# src/utils/cache.py
from __future__ import annotations

import json
import os
from typing import Dict


class TranslationCache:
    """
    Простая файловая память переводов.
    Хранит пары "исходный_текст (после защиты токенов) → переведённый_текст".
    """

    def __init__(self, path: str):
        self.path = path
        self._data: Dict[str, str] = {}
        self._loaded = False

    # ---- базовые операции ----
    def load(self) -> None:
        """Загрузка кэша с диска (один раз)."""
        if self._loaded:
            return
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}
        self._loaded = True

    def save(self) -> None:
        """Сохранение кэша на диск."""
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    # ---- интерфейс для работы ----
    def get(self, src: str) -> str | None:
        """Вернуть перевод по исходной строке (или None)."""
        return self._data.get(src)

    def put(self, src: str, dst: str) -> None:
        """Сохранить перевод в память."""
        self._data[src] = dst

    def __len__(self) -> int:
        return len(self._data)

    # ---- удобные свойства ----
    @property
    def data(self) -> Dict[str, str]:
        return self._data
