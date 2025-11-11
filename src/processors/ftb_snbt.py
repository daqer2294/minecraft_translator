from __future__ import annotations
import os
import re
from typing import Iterable, Callable

from .. import config
from ..utils.helpers import ensure_dir_for_file

# --------- Вспомогательные регекспы ---------
# строковый литерал: "...." или '....' c поддержкой экранирования внутри
_STR_TOKEN = r'(?P<q>["\'])(?P<txt>(?:\\.|(?!\1).)*?)\1'

# список строк вида ["a","b",'c'] — допускаем пробелы/переводы строк внутри
_LIST_OF_STRINGS = r'\[\s*(?:' + _STR_TOKEN + r'\s*(?:,\s*' + _STR_TOKEN + r'\s*)*)?\]'

# ключи, которые считаем «текстовыми» (берём из config, но готовим регексп один раз)
_KEYS = tuple(sorted(config.FTB_TEXT_KEYS))
_KEYS_RE = r'(?P<key>' + '|'.join(re.escape(k) for k in _KEYS) + r')'

# основной паттерн:
# key :  "str"  |  'str'  |  ["str","str2", ...]
_FIELD_PATTERN = re.compile(
    _KEYS_RE + r'\s*:\s*(?P<val>(' + _STR_TOKEN + r')|(' + _LIST_OF_STRINGS + r'))',
    flags=re.DOTALL,
)

# Внутри списка строк находим отдельные литералы
_STR_IN_LIST = re.compile(_STR_TOKEN, flags=re.DOTALL)

# --------- Утилиты экранирования ---------

def _unescape(s: str, quote: str) -> str:
    """Снимает экранирование только необходимых символов для выбранных кавычек."""
    # SNBT по сути Java-строки. Мы используем минимально-достаточное преобразование,
    # чтобы сохранить исходный вид.
    s = s.replace(r'\\', '\\')  # сначала «схлопываем» двойные бэкслеши
    if quote == '"':
        s = s.replace(r'\"', '"')
    else:
        s = s.replace(r"\'", "'")
    return s

def _escape(s: str, quote: str) -> str:
    """Экранируем только символ кавычек и обратный слеш для сохранения синтаксиса."""
    s = s.replace('\\', r'\\')
    if quote == '"':
        s = s.replace('"', r'\"')
    else:
        s = s.replace("'", r"\'")
    return s

# --------- Основная логика ---------

def _translate_scalar(s: str, translator) -> str:
    """Перевод одной строки (без кавычек)."""
    if not s.strip():
        return s
    return translator.translate(s, target_lang=config.TARGET_LANG)

def _translate_str_token(match: re.Match, translator) -> str:
    """Перевод одного строкового литерала (совпадение _STR_TOKEN)."""
    q = match.group('q')               # исходные кавычки
    raw = match.group('txt')           # содержимое без внешних кавычек
    plain = _unescape(raw, q)
    translated = _translate_scalar(plain, translator)
    escaped = _escape(translated, q)
    return f'{q}{escaped}{q}'

def _translate_list_of_strings(s: str, translator) -> str:
    """Перевод списка строк. На вход подстрока формата [ "a", 'b', ... ]."""
    # Заменяем каждый литерал отдельно, сохраняя запятые/пробелы как есть.
    def repl(m: re.Match) -> str:
        return _translate_str_token(m, translator)
    return _STR_IN_LIST.sub(repl, s)

def _translate_field_value(val_text: str, translator) -> str:
    """Определяем, одиночная строка или список строк — и переводим."""
    vt = val_text.strip()
    if not vt:
        return val_text
    # одиночная строка?
    if (vt.startswith('"') and vt.endswith('"')) or (vt.startswith("'") and vt.endswith("'")):
        # матчним 1 токен
        m = re.fullmatch(_STR_TOKEN, vt, flags=re.DOTALL)
        if m:
            return _translate_str_token(m, translator)
        return val_text  # на всякий случай — оставим как есть
    # список?
    if vt.startswith('[') and vt.endswith(']'):
        return _translate_list_of_strings(val_text, translator)
    # иное — не трогаем
    return val_text

def translate_ftb_snbt_text(text: str, translator) -> str:
    """
    Переводит только значения у ключей из FTB_TEXT_KEYS.
    Возвращает модифицированный SNBT-текст.
    """
    def repl(m: re.Match) -> str:
        key = m.group('key')
        full_val = m.group('val')
        new_val = _translate_field_value(full_val, translator)
        # сохраняем исходные пробелы вокруг двоеточия — берём всё до val из match.group(0)
        # структура group(0) = <key>   :   <val>  — мы заменяем только <val>
        start, end = m.span('val')
        g0 = m.group(0)
        prefix = g0[: start - m.start()]
        return prefix + new_val

    return _FIELD_PATTERN.sub(repl, text)

def translate_ftb_snbt_file(src_path: str, dst_path: str, translator) -> None:
    """
    Читает .snbt, переводит значения текстовых полей, сохраняет в dst_path.
    """
    with open(src_path, "r", encoding="utf-8") as f:
        data = f.read()

    out = translate_ftb_snbt_text(data, translator)

    ensure_dir_for_file(dst_path)
    with open(dst_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
