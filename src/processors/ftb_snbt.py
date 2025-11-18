# src/processors/ftb_snbt.py
from __future__ import annotations

import re

from .. import config
from ..utils.helpers import ensure_dir_for_file, is_probably_text

# ---------- строковые токены ----------

# Именованный токен — когда реально переводим строку
_STR_TOKEN = r'(?P<q>["\'])(?P<txt>(?:\\.|(?!\1).)*?)\1'

# Неименованный токен — для общих паттернов
_STR_TOKEN_NC = r'(?:"(?:\\.|[^"])*"|\'(?:\\.|[^\'])*\')'

# Список строк вида ["a","b", ...]
_LIST_OF_STRINGS_NC = (
    r'\[\s*(?:' + _STR_TOKEN_NC + r'(?:\s*,\s*' + _STR_TOKEN_NC + r')*)?\s*\]'
)

# Ключи FTB, которые считаем текстовыми
_KEYS = tuple(sorted(config.FTB_TEXT_KEYS))
_KEYS_RE = r'(?P<key>' + '|'.join(map(re.escape, _KEYS)) + r')'

# key : "str" | 'str' | ["a","b",...]
_FIELD_PATTERN = re.compile(
    _KEYS_RE + r'\s*:\s*(?P<val>(' + _STR_TOKEN_NC + r')|(' + _LIST_OF_STRINGS_NC + r'))',
    flags=re.DOTALL,
)

# Для прохода по списку строк
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


# ---------- перевод отдельной строки ----------

def _translate_scalar(s: str, translator) -> str:
    """
    Консервативный перевод одиночной строки из .snbt:
    - пропускаем пустое
    - пропускаем явно нетекстовое по эвристике is_probably_text
    - остальное отправляем в переводчик
    """
    if not s.strip():
        return s

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
    """
    Перевод списка строк ["...", "..."] — обрабатываем каждый элемент.
    """
    def repl(mm: re.Match) -> str:
        return _translate_str_token(mm, translator)

    return _STR_IN_LIST.sub(repl, s)


def _translate_field_value(val_text: str, translator) -> str:
    """
    Перевод значения текстового поля FTB:
      - одиночная строка
      - список строк
    Остальное не трогаем.
    """
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

    # прочие конструкции не трогаем
    return val_text


def translate_ftb_snbt_text(text: str, translator) -> str:
    """
    Переводит только поля с ключами из config.FTB_TEXT_KEYS.
    Остальной SNBT остаётся неизменным.
    """
    def repl(m: re.Match) -> str:
        start, _ = m.span('val')
        g0 = m.group(0)
        new_val = _translate_field_value(m.group('val'), translator)
        # подменяем только кусок значения, остальное (ключ, двоеточие) оставляем как было
        return g0[: start - m.start()] + new_val

    return _FIELD_PATTERN.sub(repl, text)


def translate_ftb_snbt_file(src_path: str, dst_path: str, translator) -> None:
    """
    Мягкая обработка ошибок:
      - если всё ок — пишем переведённый текст
      - если что-то пошло не так — логирует caller, а мы просто
        сохраняем исходное содержимое (английский, но файл целый)
    """
    with open(src_path, "r", encoding="utf-8") as f:
        data = f.read()

    try:
        out = translate_ftb_snbt_text(data, translator)
    except Exception as e:
        # не ломаем файл, просто оставляем как есть
        print(f"[WARN][snbt-soft] {src_path}: {e}")
        out = data

    ensure_dir_for_file(dst_path)
    with open(dst_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)


# Алиас, который ищет mirrorer
def translate_snbt_file(src_path: str, dst_path: str, translator) -> None:
    translate_ftb_snbt_file(src_path, dst_path, translator)