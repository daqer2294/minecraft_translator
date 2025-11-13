# src/processors/ftb_snbt.py
from __future__ import annotations
import re
import json

from .. import config
from ..utils.helpers import ensure_dir_for_file, is_probably_text

# ---------- строковые токены ----------
# Именованный токен — используем когда реально переводим строку
_STR_TOKEN = r'(?P<q>["\'])(?P<txt>(?:\\.|(?!\1).)*?)\1'

# Неименованный токен — только для больших паттернов (без конфликтов имён)
# "..." или '...' с экранированием
_STR_TOKEN_NC = r'(?:"(?:\\.|[^"])*"|\'(?:\\.|[^\'])*\')'

# Список строк из таких токенов (без имён групп)
_LIST_OF_STRINGS_NC = (
    r'\[\s*(?:' + _STR_TOKEN_NC + r'(?:\s*,\s*' + _STR_TOKEN_NC + r')*)?\s*\]'
)

# Ключи, которые считаем текстовыми
_KEYS = tuple(sorted(config.FTB_TEXT_KEYS))
_KEYS_RE = r'(?P<key>' + '|'.join(map(re.escape, _KEYS)) + r')'

# key :  "str" | 'str' | ["a","b",...]
_FIELD_PATTERN = re.compile(
    _KEYS_RE + r'\s*:\s*(?P<val>(' + _STR_TOKEN_NC + r')|(' + _LIST_OF_STRINGS_NC + r'))',
    flags=re.DOTALL,
)

# Для прохода по списку строк — здесь можно использовать именованные группы
_STR_IN_LIST = re.compile(_STR_TOKEN, flags=re.DOTALL)

# ---------- экранирование ----------
def _unescape(s: str, quote: str) -> str:
    s = s.replace(r'\\', '\\')
    if quote == '"':
        s = s.replace(r'\"', '"')
    else:
        s = s.replace(r"\'", "'")
    return s


def _escape(s: str, quote: str) -> str:
    s = s.replace('\\', r'\\')
    if quote == '"':
        s = s.replace('"', r'\"')
    else:
        s = s.replace("'", r"\'")
    return s


# ---------- вспомогательный хак для Lore / text-компонентов ----------
def _maybe_translate_mc_text_component(s: str, translator) -> str:
    """
    Пытаемся распознать строку как JSON-вложение вида {"text": "..."}.
    Если получилось — переводим только поле "text" и собираем JSON обратно.
    Если нет — возвращаем исходную строку.
    """
    # быстрый фильтр
    if '"text"' not in s:
        return s

    try:
        obj = json.loads(s)
    except Exception:
        return s

    txt = obj.get("text")
    if not isinstance(txt, str):
        return s

    if not is_probably_text(txt, config.SAFE_MAX_LEN):
        return s

    translated = translator.translate(txt, target_lang=config.TARGET_LANG)
    obj["text"] = translated

    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return s


# ---------- перевод ----------
def _translate_scalar(s: str, translator) -> str:
    """
    Перевод одного текстового литерала:
      1) сначала пробуем выцепить вложенный JSON {"text": "..."}
      2) если не похоже — обычная эвристика is_probably_text
    """
    if not s.strip():
        return s

    # 1. Lore / JSON-текст-компоненты
    new_s = _maybe_translate_mc_text_component(s, translator)
    if new_s is not s:
        return new_s

    # 2. Обычный текст
    if not is_probably_text(s, config.SAFE_MAX_LEN):
        return s

    return translator.translate(s, target_lang=config.TARGET_LANG)


def _translate_str_token(m: re.Match, translator) -> str:
    q = m.group('q')
    raw = m.group('txt')
    plain = _unescape(raw, q)
    out = _translate_scalar(plain, translator)
    return f'{q}{_escape(out, q)}{q}'


def _translate_list_of_strings(s: str, translator) -> str:
    def repl(mm: re.Match) -> str:
        return _translate_str_token(mm, translator)

    return _STR_IN_LIST.sub(repl, s)


def _translate_field_value(val_text: str, translator) -> str:
    vt = val_text.strip()
    if not vt:
        return val_text

    # одиночный литерал?
    if (vt.startswith('"') and vt.endswith('"')) or (vt.startswith("'") and vt.endswith("'")):
        m = re.fullmatch(_STR_TOKEN, vt, flags=re.DOTALL)
        return _translate_str_token(m, translator) if m else val_text

    # список строк?
    if vt.startswith('[') and vt.endswith(']'):
        return _translate_list_of_strings(val_text, translator)

    return val_text  # другое не трогаем


def translate_ftb_snbt_text(text: str, translator) -> str:
    def repl(m: re.Match) -> str:
        start, _ = m.span('val')
        g0 = m.group(0)
        new_val = _translate_field_value(m.group('val'), translator)
        return g0[: start - m.start()] + new_val

    return _FIELD_PATTERN.sub(repl, text)


def translate_ftb_snbt_file(src_path: str, dst_path: str, translator) -> None:
    with open(src_path, "r", encoding="utf-8") as f:
        data = f.read()

    out = translate_ftb_snbt_text(data, translator)
    ensure_dir_for_file(dst_path)

    with open(dst_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)


# Алиас, который ищет mirrorer
def translate_snbt_file(src_path: str, dst_path: str, translator) -> None:
    translate_ftb_snbt_file(src_path, dst_path, translator)
