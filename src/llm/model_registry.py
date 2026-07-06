# src/llm/model_registry.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

# =============================================================================
# Реестр локальных GGUF-моделей для перевода Minecraft-модпаков (en → ru/...).
#
# Выбраны модели семейства Qwen2.5-Instruct: сильный многоязычный перевод,
# аккуратно держат плейсхолдеры/§-коды/JSON, есть готовые GGUF-сборки.
# Квант Q4_K_M — лучший баланс качество/размер для CPU и потребительских GPU.
#
# ВНИМАНИЕ: hf_repo / hf_filename указывают на публичные GGUF-репозитории
# bartowski (стабильная схема именования файлов). Размеры (size_mb) —
# приблизительные для Q4_K_M и используются для оценки места и проверки
# целостности; перед реальным скачиванием их стоит сверить на HF (см.
# model_downloader — там есть проверка по фактическому Content-Length).
#
# tier:
#   "light"    — 1.5–3B, для CPU без GPU (быстро, экономно);
#   "standard" — 7B, если распознано мощное железо (GPU 6GB+ VRAM или
#                8+ ядер и 16GB+ RAM);
#   "complex"  — используется ТОЛЬКО для сложных строк (лор/квесты) при
#                двумерном роутинге; по умолчанию совпадает со standard (7B),
#                но можно выбрать 14B на очень мощном железе.
# =============================================================================


@dataclass(frozen=True)
class ModelSpec:
    id: str                 # стабильный идентификатор (ключ в конфиге/кэше)
    hf_repo: str            # HuggingFace repo_id
    hf_filename: str        # конкретный GGUF-файл (quant)
    size_mb: int            # размер файла в МиБ (для UI и оценки места)
    tier: str               # "light" | "standard" | "complex"
    min_ram_mb: int         # рекомендуемый минимум ОЗУ/VRAM для запуска
    context_length: int     # max context модели (токены)
    label: str = ""         # человекочитаемое имя для GUI
    # ТОЧНЫЙ размер файла (HTTP Content-Length с HF). Используется в
    # is_downloaded() для надёжной проверки завершённости (точное совпадение,
    # а не грубый порог по size_mb). 0 → фоллбек на size_mb.
    size_bytes: int = 0

    @property
    def display(self) -> str:
        return self.label or self.id


REGISTRY: List[ModelSpec] = [
    # ---------------- light ----------------
    ModelSpec(
        id="qwen2.5-1.5b-instruct-q4_k_m",
        hf_repo="bartowski/Qwen2.5-1.5B-Instruct-GGUF",
        hf_filename="Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
        size_mb=941,               # реально 940.4 МиБ
        size_bytes=986048768,      # точный Content-Length с HF
        tier="light",
        min_ram_mb=3000,
        context_length=32768,
        label="Qwen2.5 1.5B Instruct (Q4_K_M) — ультралёгкая",
    ),
    ModelSpec(
        id="qwen2.5-3b-instruct-q4_k_m",
        hf_repo="bartowski/Qwen2.5-3B-Instruct-GGUF",
        hf_filename="Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        size_mb=1841,              # реально 1840.5 МиБ
        size_bytes=1929903264,     # точный Content-Length с HF
        tier="light",
        min_ram_mb=4000,
        context_length=32768,
        label="Qwen2.5 3B Instruct (Q4_K_M) — лёгкая (по умолчанию)",
    ),
    # ---------------- standard ----------------
    ModelSpec(
        id="qwen2.5-7b-instruct-q4_k_m",
        hf_repo="bartowski/Qwen2.5-7B-Instruct-GGUF",
        hf_filename="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        size_mb=4467,              # реально 4466.1 МиБ
        size_bytes=4683074240,     # точный Content-Length с HF
        tier="standard",
        min_ram_mb=8000,
        context_length=32768,
        label="Qwen2.5 7B Instruct (Q4_K_M) — мощная",
    ),
    # ---------------- complex (опционально, только для лора/квестов) ----------------
    ModelSpec(
        id="qwen2.5-14b-instruct-q4_k_m",
        hf_repo="bartowski/Qwen2.5-14B-Instruct-GGUF",
        hf_filename="Qwen2.5-14B-Instruct-Q4_K_M.gguf",
        size_mb=8572,              # реально 8571.7 МиБ
        size_bytes=8988110976,     # точный Content-Length с HF
        tier="complex",
        min_ram_mb=16000,
        context_length=32768,
        label="Qwen2.5 14B Instruct (Q4_K_M) — максимальная (сложные тексты)",
    ),
]

# Модель по умолчанию для каждого тира.
# complex по умолчанию = standard (7B): не требует топового железа, но заметно
# лучше light на лоре/квестах. 14B — осознанный ручной выбор.
_DEFAULT_IDS: Dict[str, str] = {
    "light": "qwen2.5-3b-instruct-q4_k_m",
    "standard": "qwen2.5-7b-instruct-q4_k_m",
    "complex": "qwen2.5-7b-instruct-q4_k_m",
}

_BY_ID: Dict[str, ModelSpec] = {m.id: m for m in REGISTRY}


def all_models() -> List[ModelSpec]:
    return list(REGISTRY)


def get_by_id(model_id: str) -> Optional[ModelSpec]:
    if not model_id:
        return None
    return _BY_ID.get(model_id)


def list_by_tier(tier: str) -> List[ModelSpec]:
    return [m for m in REGISTRY if m.tier == tier]


def default_for_tier(tier: str) -> Optional[ModelSpec]:
    """
    Модель по умолчанию для тира. Для неизвестного тира возвращаем light.
    """
    mid = _DEFAULT_IDS.get(tier) or _DEFAULT_IDS["light"]
    return _BY_ID.get(mid)
