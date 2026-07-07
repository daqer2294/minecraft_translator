# src/llm/ollama_probe.py
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

# =============================================================================
# Автодетект локально запущенной Ollama.
#
# Ollama слушает по умолчанию http://localhost:11434 и даёт:
#   - /api/tags   → список установленных моделей (используем для детекта);
#   - /v1/...      → OpenAI-совместимый эндпоинт (используем для инференса через
#                    существующий OpenAICompatibleClient, без ключа).
#
# Это ОПЦИОНАЛЬНАЯ, некритичная проверка: короткий таймаут, любые ошибки →
# "не найдено", без исключений наружу.
# =============================================================================

OLLAMA_DEFAULT_URL = "http://localhost:11434"

# Рекомендованная модель для перевода (лёгкая, многоязычная).
RECOMMENDED_OLLAMA_MODEL = "qwen2.5:3b"


@dataclass
class OllamaModel:
    name: str            # напр. "qwen2.5:3b"
    size_bytes: int = 0


@dataclass
class OllamaStatus:
    available: bool               # ответила ли Ollama
    base_url: str                 # база (без /v1), напр. http://localhost:11434
    models: List[OllamaModel] = field(default_factory=list)
    error: str = ""              # текст ошибки, если не ответила (для диагностики)


def probe_ollama(base_url: str = OLLAMA_DEFAULT_URL, timeout: float = 2.0) -> OllamaStatus:
    """
    Пытается достучаться до Ollama и получить список моделей.
    Никогда не бросает — при любой проблеме возвращает available=False.
    """
    url = base_url.rstrip("/") + "/api/tags"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "minecraft_translator"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # ConnectionRefused / timeout / DNS / bad JSON — всё сюда
        return OllamaStatus(available=False, base_url=base_url, error=str(e))

    models: List[OllamaModel] = []
    for m in (data.get("models") or []):
        name = (m.get("name") or m.get("model") or "").strip()
        if not name:
            continue
        try:
            size = int(m.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        models.append(OllamaModel(name=name, size_bytes=size))

    return OllamaStatus(available=True, base_url=base_url.rstrip("/"), models=models)


def suggest_model(models: List[OllamaModel]) -> Optional[str]:
    """
    Разумный дефолт из установленных моделей для перевода:
    предпочитаем qwen2.5 → qwen → gemma/llama → первую попавшуюся.
    """
    if not models:
        return None
    names = [m.name for m in models]
    for needle in ("qwen2.5", "qwen", "gemma", "llama", "mistral"):
        for n in names:
            if needle in n.lower():
                return n
    return names[0]


def recommended_pull_cmd() -> str:
    """Команда установки рекомендованной модели."""
    return f"ollama pull {RECOMMENDED_OLLAMA_MODEL}"
