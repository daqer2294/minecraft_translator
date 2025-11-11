# src/config.py
from __future__ import annotations
import os
import json  # для secrets.json

# Целевой язык локализации Minecraft (формат MC)
TARGET_LANG = os.environ.get("TARGET_LANG", "ru_ru")

# Провайдер перевода: "openai" | "ollama" | "dry"
TRANSLATOR_PROVIDER = os.environ.get("TRANSLATOR_PROVIDER", "openai")
TRANSLATOR_MODEL = os.environ.get("TRANSLATOR_MODEL", "gpt-4o-mini")

# Ключи/эндпоинты
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# ===== Загрузка ключа из secrets.json (если есть) =====
import sys

def _base_dir_for_user_files() -> str:
    # если приложение собрано (PyInstaller), базой считаем папку исполняемого файла
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # иначе – корень проекта
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

BASE_DIR = _base_dir_for_user_files()
SECRETS_PATH = os.path.join(BASE_DIR, "secrets.json")

try:
    if os.path.exists(SECRETS_PATH):
        with open(SECRETS_PATH, "r", encoding="utf-8") as _sf:
            _secrets = json.load(_sf)
            if not OPENAI_API_KEY:
                OPENAI_API_KEY = _secrets.get("OPENAI_API_KEY", OPENAI_API_KEY)
except Exception:
    pass
# =====================================================

# Кэш переводов
DEFAULT_CACHE_PATH = os.environ.get("TRANSLATIONS_CACHE", "translations_cache.json")

# Ограничители/эвристики
SAFE_MAX_LEN = 800
RATE_LIMIT_SLEEP = 0.4
INCLUDE_KUBEJS_JS = os.environ.get("INCLUDE_KUBEJS_JS", "0") == "1"

# Ключи текста в FTB Quests .snbt
FTB_TEXT_KEYS = {
    "title", "subtitle", "description", "text", "message",
    "chapter", "task", "hint", "note", "body", "book_text", "page_text"
}

# 🔥 Ключи текста для «общих» JSON (tips, patchouli и пр.)
GENERIC_TEXT_KEYS = {
    "title", "name", "subtitle", "text", "message", "description",
    "tooltip", "note", "hint", "summary", "landing_text", "contents"
}
