# src/utils/helpers.py
import os
import re
import json


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
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def write_json(path: str, data: dict):
    """Безопасная запись JSON с авто-созданием директорий."""
    ensure_dir_for_file(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
