# src/utils/helpers.py
import os
import re
import json
from typing import Optional

from .markup import mask


# --- Проверка, что строка похожа на текст ---
RE_NAMESPACE = re.compile(r"[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+")   # modid:item, namespace:key
RE_PLACEHOLDER = re.compile(r"%[sdifx]|%\d*\$[sdifx]|\{[\w\.]+\}")  # %s, %1$s, {count}, {player}
RE_HEAVY_SYMBOLS = re.compile(r"[{}<>$%^\\\[\]|`~]")
RE_LATIN = re.compile(r"[A-Za-z]")

def is_probably_text(s: str, max_len: int = 800) -> bool:
    """Возвращает True, если строка похожа на обычный текст, который стоит перевести."""
    if not isinstance(s, str) or not s:
        return False
    if len(s) > max_len:
        return False
    if not RE_LATIN.search(s):  # нет латиницы — не похоже на английский текст
        return False
    if RE_HEAVY_SYMBOLS.search(s):
        return False
    if RE_NAMESPACE.search(s):  # modid:item → не трогаем
        return False
    return True


# --- Безопасное создание директорий для файла ---
def ensure_dir_for_file(path: str):
    """Создаёт директории для указанного пути, если их ещё нет."""
    os.makedirs(os.path.dirname(path), exist_ok=True)


# --- Работа с JSON ---
def read_json(path: str) -> dict:
    """Безопасное чтение JSON."""
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)

def write_json(path: str, data: dict):
    """Безопасная запись JSON с авто-созданием директорий."""
    ensure_dir_for_file(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# --- Чтение lang-файлов ---
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def parse_lang_bytes(raw: bytes) -> Optional[dict]:
    """
    Разбирает lang-файл так же мягко, как игра (Gson): BOM и висячая запятая
    допустимы. Возвращает dict или None, если это всё же не JSON-объект.
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    for candidate in (text, _TRAILING_COMMA.sub(r"\1", text)):
        try:
            data = json.loads(candidate, strict=False)
        except ValueError:
            continue
        return data if isinstance(data, dict) else None
    return None


# --- Что модели отправлять незачем ---
_RE_URL = re.compile(r"(?:https?://|www\.)\S+")
_RE_NAMESPACED_ID = re.compile(r"[a-z0-9_.\-]+:[a-z0-9_/.\-]+")
_RE_TRANSLATION_KEY = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+)+")
_RE_SNAKE_ID = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)+")
_RE_NUMERIC_UNIT = re.compile(r"[\d.,:/\- ]*[a-zA-Z]{0,2}[\d.,:/\- ]*")
_RE_DIGIT = re.compile(r"\d")
_RE_SINGLE_LETTER = re.compile(r"[A-Za-z]")


def is_untranslatable(text: str) -> bool:
    """Нет слов для перевода: пустое, только разметка, ссылка, ID, ключ, число с единицей, одна буква."""
    s = text.strip()
    if not RE_LATIN.search(mask(s).core):
        return True
    if _RE_URL.fullmatch(s) or _RE_NAMESPACED_ID.fullmatch(s):
        return True
    if _RE_TRANSLATION_KEY.fullmatch(s) or _RE_SNAKE_ID.fullmatch(s):
        return True
    if _RE_NUMERIC_UNIT.fullmatch(s) and _RE_DIGIT.search(s):
        return True
    return _RE_SINGLE_LETTER.fullmatch(s) is not None
