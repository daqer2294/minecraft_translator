# src/processors/lang_json.py
from __future__ import annotations
import os
import json
from ..utils.helpers import is_probably_text, ensure_dir_for_file
from .. import config


def _translate_map(data: dict, translator) -> dict:
    """Общий перевод словаря lang: key->value (переводим только текстовые value)."""
    result = {}
    for key, value in data.items():
        if isinstance(value, str) and is_probably_text(value, config.SAFE_MAX_LEN):
            translated = translator.translate(value, target_lang=config.TARGET_LANG)
            result[key] = translated
        else:
            result[key] = value
    return result


def translate_lang_json(input_path: str, output_path: str, translator) -> None:
    """
    Переводит JSON-файл формата Minecraft lang (en_us.json → ru_ru.json) с диска.
    """
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = _translate_map(data, translator)
    ensure_dir_for_file(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[OK] Saved: {output_path}")


def translate_lang_obj(obj: dict, output_path: str, translator) -> None:
    """
    Переводит уже загруженный dict en_us.json (удобно для чтения из .jar) и пишет ru_ru.json.
    """
    result = _translate_map(obj, translator)
    ensure_dir_for_file(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[OK] Saved: {output_path}")
