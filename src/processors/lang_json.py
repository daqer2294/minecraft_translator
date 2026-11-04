# src/processors/lang_json.py
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from ..utils.helpers import ensure_dir_for_file, parse_lang_bytes


def has_official_translation(key: str, en: dict, official: dict) -> bool:
    """Ключ уже переведён, если в официальном файле есть значение, отличное от английского."""
    return key in official and official[key] != en.get(key)


def keys_to_translate(en: dict, official: dict) -> List[str]:
    return [
        k for k, v in en.items()
        if isinstance(v, str) and not has_official_translation(k, en, official)
    ]


def merge_lang(en: dict, official: dict, translated: Dict[str, str]) -> dict:
    """
    Официальные значения не меняются. Новый перевод добавляется только там, где он
    отличается от оригинала: на отсутствующем ключе игра сама покажет en_us.
    Порядок ключей как в en_us, ключи только из официального файла идут в конце.
    """
    merged = {}
    for key, source in en.items():
        if has_official_translation(key, en, official):
            merged[key] = official[key]
        elif key in translated and translated[key] != source:
            merged[key] = translated[key]
    for key, value in official.items():
        if key not in en:
            merged[key] = value
    return merged


def write_lang(dst_path: str, data: dict) -> None:
    ensure_dir_for_file(dst_path)
    with open(dst_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_lang_file(path: str) -> Optional[dict]:
    with open(path, "rb") as f:
        return parse_lang_bytes(f.read())


def translate_lang_json(src_path: str, dst_path: str, translator, official_path: Optional[str] = None) -> int:
    """
    lang/en_us.json -> <язык>.json на диске. Возвращает число новых переводов.
    Без новых переводов файл не создаётся: игра и так возьмёт официальный.
    """
    en = read_lang_file(src_path)
    if en is None:
        raise ValueError("не JSON-объект")

    official = {}
    if official_path and os.path.exists(official_path):
        official = read_lang_file(official_path)
        if official is None:
            raise ValueError(f"официальный перевод не читается: {official_path}")

    keys = keys_to_translate(en, official)
    if not keys:
        return 0

    outs = translator.translate_many([en[k] for k in keys])
    translated = dict(zip(keys, outs))
    fresh = sum(translated[k] != en[k] for k in keys)
    if not fresh:
        return 0

    write_lang(dst_path, merge_lang(en, official, translated))
    return fresh


def translate_lang_obj(obj: dict, output_path: str, translator) -> None:
    """Перевод уже загруженного dict en_us.json и запись результата."""
    keys = keys_to_translate(obj, {})
    if keys:
        outs = translator.translate_many([obj[k] for k in keys])
        write_lang(output_path, merge_lang(obj, {}, dict(zip(keys, outs))))
