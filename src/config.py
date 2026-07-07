# src/config.py
from __future__ import annotations
import os
import json
import sys
from dataclasses import dataclass

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
# (устаревшие поля — оставлены для обратной совместимости; актуальная
#  конфигурация теперь в ProviderConfig / PROVIDER ниже)
TRANSLATOR_PROVIDER = os.environ.get("TRANSLATOR_PROVIDER", "openai")  # legacy
TRANSLATOR_MODEL = os.environ.get("TRANSLATOR_MODEL", "gpt-4o-mini")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
# Закрывает R-4 / Q-1: base_url теперь конфигурируем (OpenAI, DeepSeek, Qwen,
# llama.cpp server, LM Studio, Ollama /v1, vLLM ...).
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# Локальный инференс (llama.cpp / GGUF)
LOCAL_MODEL_PATH = os.environ.get("LOCAL_MODEL_PATH", "")       # путь к .gguf (in-process)
LOCAL_SERVER_URL = os.environ.get("LOCAL_SERVER_URL", "")       # URL llama-server (server mode)
LOCAL_MODEL_LABEL = os.environ.get("LOCAL_MODEL_LABEL", "local-gguf")

# Режим работы переводчика: "local" | "external" | "hybrid"
TRANSLATOR_MODE = os.environ.get("TRANSLATOR_MODE", "local")

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


# ========== Конфигурация провайдера (A-1) ==========
@dataclass
class ProviderConfig:
    """
    Единая конфигурация источника перевода.

    mode:
      - "local"    — всё через локальную GGUF-модель (llama.cpp). Ключ API не нужен.
      - "external" — всё через внешний OpenAI-совместимый API.
      - "hybrid"   — массовые строки локально, «сложные» — через внешний API.
      - "ollama"   — локально запущенная Ollama (OpenAI-совместимый эндпоинт),
                     использует уже установленные там модели; ключ не нужен.
    """
    mode: str = "local"

    # локальный инференс
    local_model_path: str = ""       # путь к .gguf для in-process (llama-cpp-python)
    local_server_url: str = ""       # URL llama-server (OpenAI-совместимый) — server mode
    local_model: str = "local-gguf"  # ярлык модели (для server mode / логов)

    # внешний OpenAI-совместимый API
    external_base_url: str = "https://api.openai.com"
    external_api_key: str = ""
    external_model: str = "gpt-4o-mini"

    # локальная Ollama (OpenAI-совместимый эндпоинт /v1, ключ не нужен)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = ""           # имя выбранной модели, напр. "qwen2.5:3b"

    # авто-выбор модели по железу (STEP 1-2) + двумерный роутинг (STEP 5)
    tier: str = "light"          # выбранный тир железа: "light" | "standard"
    light_model_id: str = ""     # id из model_registry (пусто → default_for_tier("light"))
    standard_model_id: str = ""  # id из model_registry (пусто → default_for_tier("standard"))

    # prefix/prompt caching в server mode llama-server (STEP 4)
    prompt_cache: bool = True


# Экземпляр по умолчанию: режим "local", ключ/URL подтягиваются из env/secrets.
PROVIDER = ProviderConfig(
    mode=TRANSLATOR_MODE,
    local_model_path=LOCAL_MODEL_PATH,
    local_server_url=LOCAL_SERVER_URL,
    local_model=LOCAL_MODEL_LABEL,
    external_base_url=OPENAI_BASE_URL,
    external_api_key=OPENAI_API_KEY,
    external_model=TRANSLATOR_MODEL,
)


# ========== Каталог данных приложения (модели, проба железа) ==========
# Сюда кладём скачанные GGUF-модели и кэш пробы железа, чтобы не мусорить в
# рабочей папке и не гонять пробу/скачивание при каждом запуске.
APP_DATA_DIR = os.environ.get("MC_TRANSLATOR_HOME") or os.path.join(
    os.path.expanduser("~"), ".minecraft_translator"
)
MODELS_DIR = os.path.join(APP_DATA_DIR, "models")
HARDWARE_CACHE_PATH = os.path.join(APP_DATA_DIR, "hardware.json")


def ensure_app_dirs() -> None:
    """Создать каталоги приложения (идемпотентно)."""
    os.makedirs(MODELS_DIR, exist_ok=True)


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
