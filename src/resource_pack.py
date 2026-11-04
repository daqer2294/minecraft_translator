# src/resource_pack.py
"""
Готовый ресурспак с переводом: zip с pack.mcmeta и assets/.

Пак собирается в памяти и пишется одним файлом только в конце полного прогона.
После «Стоп» zip не появляется: готовые переводы лежат в кэше, повторный запуск
возьмёт их оттуда и соберёт пак без лишних запросов к модели.
"""
from __future__ import annotations

import json
import os
import threading
import zipfile
from typing import Any, Dict, Iterable, Optional, Set

# 34 — формат ресурспаков Minecraft 1.21 и 1.21.1
DEFAULT_PACK_FORMAT = 34


class ResourcePackBuilder:
    def __init__(self, out_root: str, target_lang: str, pack_format: int = DEFAULT_PACK_FORMAT):
        self.zip_path = os.path.join(out_root, f"MinecraftTranslator_{target_lang}.zip")
        self.target_lang = target_lang
        self.pack_format = pack_format
        self._lock = threading.Lock()
        self._lang: Dict[str, Dict[str, str]] = {}
        self._official: Dict[str, Set[str]] = {}
        self._json: Dict[str, Any] = {}

    def add_lang(self, rel: str, data: Dict[str, str], official_keys: Iterable[str] = ()) -> None:
        """
        Разные jar могут дополнять одно пространство имён, поэтому файлы сливаются.
        Официальные ключи в пак не попадают: игра объединяет языковые файлы по ключам,
        так что они остаются в своих jar как есть, а пак добавляет только недостающее.
        Иначе пак поверх модов закрепил бы один из вариантов или перекрыл официальный
        перевод машинным из другого jar того же пространства имён.
        """
        official_keys = set(official_keys)
        with self._lock:
            self._lang.setdefault(rel, {}).update(
                {k: v for k, v in data.items() if k not in official_keys}
            )
            self._official.setdefault(rel, set()).update(official_keys)

    def _lang_files(self) -> Dict[str, Dict[str, str]]:
        files = {}
        for rel, data in self._lang.items():
            fresh = {k: v for k, v in data.items() if k not in self._official[rel]}
            if fresh:
                files[rel] = fresh
        return files

    def add_json(self, rel: str, data: Any) -> None:
        with self._lock:
            self._json[rel] = data

    def _mcmeta(self) -> Dict[str, Any]:
        return {
            "pack": {
                "pack_format": self.pack_format,
                "description": f"Minecraft Translator: {self.target_lang}",
            }
        }

    def finish(self) -> Optional[str]:
        """Пишет zip атомарно и возвращает путь; None, если добавлять в пак нечего."""
        with self._lock:
            files = {**self._json, **self._lang_files()}
            if not files:
                return None
            os.makedirs(os.path.dirname(self.zip_path), exist_ok=True)
            tmp = f"{self.zip_path}.tmp"
            try:
                with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("pack.mcmeta", json.dumps(self._mcmeta(), ensure_ascii=False, indent=2))
                    for rel in sorted(files):
                        z.writestr(rel, json.dumps(files[rel], ensure_ascii=False, indent=2))
                os.replace(tmp, self.zip_path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            return self.zip_path

    def stats(self) -> Dict[str, int]:
        with self._lock:
            lang = self._lang_files()
            return {
                "lang_files": len(lang),
                "strings": sum(len(v) for v in lang.values()),
                "other_files": len(self._json),
            }
