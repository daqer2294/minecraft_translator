# src/config.py
from __future__ import annotations
import os
import json
import sys

# ========== Speed knobs / performance ==========
MAX_WORKERS_FILES = 6          # параллельная обработка файлов
BATCH_SIZE = 100               # размер пачки строк на 1 запрос
CACHE_FALLBACKS = True         # использовать кэш переводов
RETRY_MAX_ATTEMPTS = 6
RETRY_BASE_DELAY = 2.0
RETRY_MAX_DELAY = 30.0
RETRY_JITTER = 0.25

# Максимальная длина ОДНОГО куска текста, который отправляем в модель.
# Если строка длиннее, мы режем её по предложениям на куски <= этого лимита.
MAX_CHUNK_LEN = 100

# Нужно ли сканировать JAR на наличие lang/en_us.json
SCAN_JAR_LANG = True

# ========== Язык перевода ==========
# Minecraft-формат локали, напр. "ru_ru", "de_de", "es_es"
TARGET_LANG = os.environ.get("TARGET_LANG", "ru_ru")

# Для промта нужно «человеческое» имя языка
MC_LANG_NAMES = {
    "ru_ru": "Russian",
    "en_us": "English",
    "de_de": "German",
    "fr_fr": "French",
    "es_es": "Spanish",
    "pt_br": "Brazilian Portuguese",
    "zh_cn": "Simplified Chinese",
    "ja_jp": "Japanese",
}

def get_target_lang_name() -> str:
    return MC_LANG_NAMES.get(TARGET_LANG.lower(), TARGET_LANG)

# ========== Провайдер перевода ==========
TRANSLATOR_PROVIDER = os.environ.get("TRANSLATOR_PROVIDER", "openai")  # "openai" | "ollama" | "dry"
TRANSLATOR_MODEL = os.environ.get("TRANSLATOR_MODEL", "gpt-4o-mini")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# ===== Загрузка ключа из secrets.json (если есть) =====
def _base_dir_for_user_files() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
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
    # Если секреты не прочитались — тихо игнорируем
    pass

# ========== Кэш ==========
DEFAULT_CACHE_PATH = os.environ.get("TRANSLATIONS_CACHE", "translations_cache.json")

# ========== Эвристики ==========
SAFE_MAX_LEN = 800                   # максимум длины строки, которую считаем «текстом»
RATE_LIMIT_SLEEP = 0.4
INCLUDE_KUBEJS_JS = os.environ.get("INCLUDE_KUBEJS_JS", "0") == "1"

# Ключи текста в FTB Quests .snbt
FTB_TEXT_KEYS = {
    "title", "subtitle", "description", "text", "message",
    "chapter", "task", "hint", "note", "body", "book_text", "page_text"
}

# Ключи текста для «общих» JSON (tips, patchouli и пр.)
GENERIC_TEXT_KEYS = {
    "title", "name", "subtitle", "text", "message", "description",
    "tooltip", "note", "hint", "summary", "landing_text", "contents"
}
