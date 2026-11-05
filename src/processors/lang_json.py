# src/processors/lang_json.py
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple

from ..utils.helpers import ensure_dir_for_file, parse_lang_bytes
from ..utils.markup import MARKUP_RE, is_cyrillic_lang

_CYRILLIC_RE = re.compile("[\u0400-\u052f]")
_WORD_RE = re.compile(r"[^\W\d_]{2,}")
_LATIN_WORD_RE = re.compile("[A-Za-z\u00c0-\u024f]+")
ENGLISH_MIN_WORDS = 2


def is_english_left(value: object, target_lang: str) -> bool:
    """
    В файле языка на кириллице лежит текст без кириллицы, из двух слов и больше:
    «The target is punched with an icy fist.» Одно слово («Windows») и значение без букв
    могут быть настоящим переводом, поэтому не считаются.
    """
    if not isinstance(value, str) or not is_cyrillic_lang(target_lang):
        return False
    words = _WORD_RE.findall(value)
    return (
        len(words) >= ENGLISH_MIN_WORDS
        and not _CYRILLIC_RE.search(value)
        and all(_LATIN_WORD_RE.fullmatch(word) for word in words)
    )


def has_official_translation(key: str, en: dict, official: dict, target_lang: str = "") -> bool:
    """Ключ уже переведён, если в официальном файле есть значение, отличное от английского и не английское."""
    return key in official and official[key] != en.get(key) and not is_english_left(official[key], target_lang)


def source_text(key: str, en: dict, official: dict, target_lang: str = "") -> str:
    """
    Что переводить. Английский текст из официального файла: его игра и показывает сейчас.
    Если в нём другая разметка, то en_us: подстановки в коде мода рассчитаны на него.
    """
    value = official.get(key)
    if is_english_left(value, target_lang) and sorted(MARKUP_RE.findall(value)) == sorted(MARKUP_RE.findall(en[key])):
        return value
    return en[key]


def is_service_key(key: str) -> bool:
    """Служебные ключи: «ali/loot_table/chests/x» и «__support_shield+»."""
    return key.startswith("__") or "/loot_table/" in key or "/loot_tables/" in key


def keys_to_translate(en: dict, official: dict, target_lang: str = "") -> List[str]:
    return [
        k for k, v in en.items()
        if isinstance(v, str) and not is_service_key(k) and not has_official_translation(k, en, official, target_lang)
    ]


def merge_lang(en: dict, official: dict, translated: Dict[str, str], target_lang: str = "") -> dict:
    """
    Официальные значения не меняются. Новый перевод добавляется только там, где он
    отличается от оригинала: на отсутствующем ключе игра сама покажет en_us.
    Порядок ключей как в en_us, ключи только из официального файла идут в конце.
    """
    merged = {}
    for key in en:
        if has_official_translation(key, en, official, target_lang):
            merged[key] = official[key]
        elif key in translated and translated[key] != source_text(key, en, official, target_lang):
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


def translate_lang_file(
    src_path: str, translator, official_path: Optional[str] = None, target_lang: str = "",
) -> Tuple[Optional[dict], int, List[str]]:
    """
    Переводит lang/en_us.json. Возвращает (результат, новых переводов, официальные ключи).
    Без новых переводов результат None: игра и так возьмёт официальный файл.
    """
    en = read_lang_file(src_path)
    if en is None:
        raise ValueError("не JSON-объект")

    official = {}
    if official_path and os.path.exists(official_path):
        official = read_lang_file(official_path)
        if official is None:
            raise ValueError(f"официальный перевод не читается: {official_path}")

    keys = keys_to_translate(en, official, target_lang)
    if not keys:
        return None, 0, []

    sources = [source_text(k, en, official, target_lang) for k in keys]
    outs = translator.translate_many(sources)
    translated = dict(zip(keys, outs))
    fresh = sum(out != source for out, source in zip(outs, sources))
    if not fresh:
        return None, 0, []

    merged = merge_lang(en, official, translated, target_lang)
    return merged, fresh, [k for k in merged if k in official and k not in translated]


def translate_lang_obj(obj: dict, output_path: str, translator) -> None:
    """Перевод уже загруженного dict en_us.json и запись результата."""
    keys = keys_to_translate(obj, {})
    if keys:
        outs = translator.translate_many([obj[k] for k in keys])
        write_lang(output_path, merge_lang(obj, {}, dict(zip(keys, outs))))
