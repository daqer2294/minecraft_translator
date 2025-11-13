# src/processors/lang_json.py
from __future__ import annotations
import json
from .. import config
from ..utils.helpers import ensure_dir_for_file


def translate_lang_json(src_path: str, dst_path: str, translator) -> None:
    """
    Прямой перевод файла lang/en_us.json -> ru_ru.json на диске.
    Переводим ВСЕ строковые значения.
    """
    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    keys = []
    values = []
    out_data = dict(data)

    for k, v in data.items():
        if isinstance(v, str):
            keys.append(k)
            values.append(v)

    if values:
        outs = translator.translate_many(values, target_lang=config.TARGET_LANG)
        for k, translated in zip(keys, outs):
            out_data[k] = translated

    ensure_dir_for_file(dst_path)
    with open(dst_path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    print(f"[OK][lang_json] {src_path} -> {dst_path}")


def translate_lang_obj(obj: dict, output_path: str, translator) -> None:
    """
    Перевод уже загруженного dict en_us.json (когда читаем прямо из .jar)
    и запись ru_ru.json.
    Переводим ВСЕ строковые значения.
    """
    keys = []
    values = []
    out_data = dict(obj)

    for k, v in obj.items():
        if isinstance(v, str):
            keys.append(k)
            values.append(v)

    if values:
        outs = translator.translate_many(values, target_lang=config.TARGET_LANG)
        for k, translated in zip(keys, outs):
            out_data[k] = translated

    ensure_dir_for_file(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    print(f"[OK][lang_obj] saved: {output_path}")
