# src/processors/generic_json.py
from __future__ import annotations
import json
from typing import Any, Dict, List
from ..utils.helpers import is_probably_text, ensure_dir_for_file
from .. import config


def _translate_value(val: Any, key_hint: str, translator):
    if isinstance(val, str):
        # Для JSON-описаний переводим:
        #   – если ключ "похож" на текстовый
        #   – ИЛИ если сама строка очень похожа на обычный текст
        if (key_hint in config.GENERIC_TEXT_KEYS or
                is_probably_text(val, config.SAFE_MAX_LEN)):
            return translator.translate(val, target_lang=config.TARGET_LANG)
        return val

    if isinstance(val, list):
        return [_translate_value(item, key_hint, translator) for item in val]

    if isinstance(val, dict):
        return _translate_obj(val, translator)

    return val


def _translate_obj(obj: Dict[str, Any], translator):
    out: Dict[str, Any] = {}
    for k, v in obj.items():
        key_hint = str(k).lower()
        out[k] = _translate_value(v, key_hint, translator)
    return out


def translate_generic_json_file(src_path: str, dst_path: str, translator) -> None:
    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    result = _translate_obj(data, translator)
    ensure_dir_for_file(dst_path)

    with open(dst_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[OK][generic_json] {src_path} -> {dst_path}")
