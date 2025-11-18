# src/processors/snbt_structured.py
from __future__ import annotations

import json
import re
from typing import Sequence, Tuple, Optional

import ftb_snbt_lib as slib
from ftb_snbt_lib.tag import Compound, List as NbtList, String as StringTag

from .. import config
from ..utils.helpers import ensure_dir_for_file, is_probably_text

# ---------- Minecraft форматирование (§) ----------

_MC_FORMAT_RE = re.compile(r'^(?:§.)+')

# строки, похожие на resource-путь / ID: modid:path/like_this-123
_RES_PATH_RE = re.compile(r'^[a-z0-9_./:-]+$')

# ключи, которые почти всегда означают человекочитаемый текст
_FORCED_TEXT_KEYS = {
    "title",
    "subtitle",
    "description",
    "desc",
    "text",
    "message",
    "lore",
    "name",
    "hover",
    "hover_text",
    "chapter_title",
}


def _split_minecraft_formatting(text: str) -> Tuple[str, str]:
    """
    '§7§o§e§oSome text' -> ('§7§o§e§o', 'Some text')
    'Some text'        -> ('', 'Some text')
    """
    m = _MC_FORMAT_RE.match(text)
    if not m:
        return "", text
    return m.group(0), text[m.end():]


# ---------- утилиты по пути ----------

def _last_semantic_key(path: Sequence[str]) -> Optional[str]:
    """
    Возвращает последний "осмысленный" сегмент пути:
    - пропускает индексы [0], [1], ...
    """
    for seg in reversed(path):
        if seg.startswith("[") and seg.endswith("]"):
            continue
        return seg
    return None


def _is_path_force_text(path: Sequence[str]) -> bool:
    """
    Поля, которые почти гарантированно текстовые и должны переводиться
    даже если строка короткая.
    """
    last = _last_semantic_key(path)
    if not last:
        return False

    if last.lower() in _FORCED_TEXT_KEYS:
        return True

    # если по пути встречаются явно текстовые контейнеры
    text_containers = {"lore", "pages", "description", "subtitle", "title", "name"}
    if any(seg.lower() in text_containers for seg in path):
        return True

    return False


def _is_path_technical(path: Sequence[str]) -> bool:
    """
    Очень мягкая фильтрация сугубо тех. полей.
    Всё, что может быть человекочитаемым (title, subtitle, description,
    pages, Lore, Name, author и т.п.) — НЕ отбрасываем.
    """
    if not path:
        return False

    last = path[-1]

    # если последний элемент — индекс [0], [1] и т.п., смотрим на предыдущий
    if last.startswith("[") and last.endswith("]") and len(path) >= 2:
        last = path[-2]

    last_lower = last.lower()

    technical_keys = {
        "id",
        "filename",
        "group",
        "icon",
        "order_index",
        "quest_links",
        "x",
        "y",
        "z",
        "pos",
        "size",
        "color",
        "background",
        "shape",
        "dimension",
    }
    if last_lower in technical_keys:
        return True

    # спец-кейс FTB Quests: tasks.*.type — это тип задачи, его нельзя переводить
    if last_lower == "type":
        if any(seg.lower() == "tasks" for seg in path):
            return True

    return False


# ---------- перевод обычного текста ----------

def _translate_plain_text(text: str, translator, path: Sequence[str]) -> str:
    """
    Перевод простой строки без JSON.

    Логика:
      1) Пустое и тех.поля — пропускаем.
      2) Если путь явно текстовый (_is_path_force_text) — переводим почти всегда.
      3) Иначе:
         - не трогаем строки, похожие на ResourceLocation / ID
         - фильтруем через is_probably_text
    """
    stripped = text.strip()
    if not stripped:
        return text

    # 1) тех. поля (id, filename, tasks.*.type, и т.п.) — никогда не переводим
    if _is_path_technical(path):
        return text

    # 2) Явно текстовые поля — переводим даже если строка короткая
    if _is_path_force_text(path):
        return translator.translate(text, target_lang=config.TARGET_LANG)

    # 3) Остальное: аккуратная эвристика

    # не трогаем строки, которые выглядят как чистый ресурсный путь / ID
    if _RES_PATH_RE.fullmatch(stripped):
        return text

    # не похоже на нормальный текст — пропускаем
    if not is_probably_text(text, config.SAFE_MAX_LEN):
        return text

    return translator.translate(text, target_lang=config.TARGET_LANG)


# ---------- перевод chat JSON-компонентов ----------

def _translate_chat_component(component, translator, path: Sequence[str]):
    """
    ФУНКЦИОНАЛЬНЫЙ (без in-place модификаций) проход по JSON chat-компоненту.
    Возвращаем новый объект, исходный не трогаем.
    """
    # dict-компонент
    if isinstance(component, dict):
        new_dict = {}

        for key, value in component.items():
            if key == "text" and isinstance(value, str):
                prefix, core = _split_minecraft_formatting(value)
                translated_core = _translate_plain_text(core, translator, path + ("text",))
                new_dict[key] = prefix + translated_core
            elif key == "with" and isinstance(value, list):
                new_dict[key] = [
                    _translate_chat_component(x, translator, path + ("with", str(i)))
                    for i, x in enumerate(value)
                ]
            else:
                new_dict[key] = _translate_chat_component(
                    value, translator, path + (key,)
                )

        return new_dict

    # list-компонент
    if isinstance(component, list):
        return [
            _translate_chat_component(x, translator, path + (f"[{i}]",))
            for i, x in enumerate(component)
        ]

    # Простая строка внутри JSON-структуры
    if isinstance(component, str):
        prefix, core = _split_minecraft_formatting(component)
        translated_core = _translate_plain_text(core, translator, path)
        return prefix + translated_core

    # Числа, bool и т.п. — не трогаем
    return component


def _try_translate_chat_json(text: str, translator, path: Sequence[str]) -> str | None:
    """
    Если строка похожа на JSON-компонент (начинается с '{' или '['),
    пробуем распарсить, рекурсивно перевести и собрать обратно.
    ЛЮБАЯ ошибка внутри → None (строка останется как есть).
    """
    stripped = text.strip()
    if not stripped:
        return None

    if not (stripped.startswith("{") or stripped.startswith("[")):
        return None

    try:
        data = json.loads(stripped)
        translated = _translate_chat_component(data, translator, path)
        return json.dumps(translated, ensure_ascii=False)
    except Exception:
        # Любая проблема с JSON → просто не трогаем эту строку
        return None


def _translate_string_value(value: str, translator, path: Sequence[str]) -> str:
    """
    Универсальный перевод значений String-тегов:
      1) сперва пытаемся как chat JSON
      2) если не получилось — обычный текст
    """
    json_res = _try_translate_chat_json(value, translator, path)
    if json_res is not None:
        return json_res

    return _translate_plain_text(value, translator, path)


# ---------- рекурсивный обход NBT-дерева ----------

def _translate_nbt_tag(tag, translator, path: Sequence[str] = ()):
    """
    Рекурсивно обходим всё дерево:
      - Compound: по ключам
      - List: по элементам
      - String: переводим
      - остальные теги: не трогаем

    ЛЮБАЯ ошибка в обработке дочернего тега не роняет весь файл —
    просто оставляем этот конкретный узел как есть.
    """
    # Compound = словарь тегов
    if isinstance(tag, Compound):
        for key in list(tag.keys()):  # фиксированный список ключей
            child = tag[key]
            child_path = path + (str(key),)
            try:
                tag[key] = _translate_nbt_tag(child, translator, child_path)
            except Exception:
                # Если конкретный ребёнок сломался — оставляем его как есть
                tag[key] = child
        return tag

    # List = список тегов
    if isinstance(tag, NbtList):
        for i in range(len(tag)):
            child = tag[i]
            child_path = path + (f"[{i}]",)
            try:
                tag[i] = _translate_nbt_tag(child, translator, child_path)
            except Exception:
                tag[i] = child
        return tag

    # String = строка, которую мы хотим перевести
    if isinstance(tag, StringTag):
        original = str(tag)
        try:
            new_value = _translate_string_value(original, translator, path)
        except Exception:
            return tag  # на всякий пожарный

        if new_value == original:
            return tag  # без изменений

        return StringTag(new_value)

    # Всё остальное (числа, массивы и т.п.) — оставляем как есть
    return tag


# ---------- публичные функции ----------

def translate_snbt_text_structured(text: str, translator) -> str:
    """
    SNBT → NBT → рекурсивный перевод строк → SNBT.
    """
    root = slib.loads(text)  # Compound
    root = _translate_nbt_tag(root, translator, path=())
    return slib.dumps(root, comma_sep=False)


def translate_snbt_file_structured(src_path: str, dst_path: str, translator) -> None:
    """
    Читает SNBT, переводит структурно, сохраняет.
    """
    with open(src_path, "r", encoding="utf-8") as f:
        data = f.read()

    out = translate_snbt_text_structured(data, translator)

    ensure_dir_for_file(dst_path)
    with open(dst_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
