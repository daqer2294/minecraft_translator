# src/processors/snbt_structured.py
from __future__ import annotations
import json
from amulet_nbt import snbt
from amulet_nbt import nbt as nbt_types

from ..utils.helpers import ensure_dir_for_file, is_probably_text
from .. import config


def _translate_chat_component(raw: str, translator):
    """
    Переводит JSON chat-component внутри SNBT строки.
    """
    try:
        obj = json.loads(raw)
    except Exception:
        return raw

    changed = False

    def walk(o):
        nonlocal changed
        if isinstance(o, dict):
            if "text" in o and isinstance(o["text"], str):
                if is_probably_text(o["text"], config.SAFE_MAX_LEN):
                    o["text"] = translator.translate(o["text"], target_lang=config.TARGET_LANG)
                    changed = True

            # вложенные поля
            for v in o.values():
                walk(v)

        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    if not changed:
        return raw
    return json.dumps(obj, ensure_ascii=False)


def _walk_and_translate(tag, translator):
    """
    Рекурсивно обходит NBT-структуру и переводит текстовые поля.
    """
    # Строка
    if isinstance(tag, nbt_types.StringTag):
        text = tag.py_data

        # Если это chat component JSON
        if text.startswith("{") and text.endswith("}"):
            tag.py_data = _translate_chat_component(text, translator)
            return

        # Если это обычный текст
        if is_probably_text(text, config.SAFE_MAX_LEN):
            tag.py_data = translator.translate(text, target_lang=config.TARGET_LANG)

    # Список
    elif isinstance(tag, nbt_types.ListTag):
        for item in tag:
            _walk_and_translate(item, translator)

    # Объект
    elif isinstance(tag, nbt_types.CompoundTag):
        for k in tag:
            _walk_and_translate(tag[k], translator)


def translate_snbt_file_structured(src_path: str, dst_path: str, translator) -> None:
    """
    SNBT → NBT → рекурсивный перевод → SNBT.
    """
    with open(src_path, "r", encoding="utf-8") as f:
        txt = f.read()

    # SNBT → NBT
    nbt_obj = snbt.loads(txt)

    # рекурсивный перевод
    _walk_and_translate(nbt_obj, translator)

    # NBT → SNBT
    out_text = snbt.dumps(nbt_obj)

    ensure_dir_for_file(dst_path)
    with open(dst_path, "w", encoding="utf-8") as f:
        f.write(out_text)
