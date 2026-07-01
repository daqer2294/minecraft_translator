# src/llm/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

# Тип одного сообщения чата: {"role": "system"|"user"|"assistant", "content": "..."}
Message = Dict[str, str]


class LLMClientError(Exception):
    """Базовая ошибка LLM-клиента."""


class RateLimitError(LLMClientError):
    """Провайдер вернул 429 / rate limit. Ретраится в Translator._retry_call."""


class LLMClient(ABC):
    """
    Единый интерфейс к любому провайдеру перевода.

    Реализации (см. A-1 из AUDIT_REPORT.md):
      - OpenAICompatibleClient — внешний OpenAI-совместимый API (ChatGPT, DeepSeek,
        Qwen, а также локальный llama.cpp server / LM Studio / Ollama /v1);
      - LocalLlamaCppClient — локальный инференс GGUF через llama-cpp-python.

    Translator работает ТОЛЬКО через этот интерфейс и ничего не знает о том,
    внешний это API или локальная модель.
    """

    @abstractmethod
    def chat(self, messages: List[Message], **kwargs: Any) -> str:
        """
        Отправить чат-сообщения и вернуть текстовый ответ модели (content).

        Обязательный контракт:
          - принимает список messages в OpenAI-формате;
          - поддерживает kwargs: temperature, max_tokens, timeout (могут игнорироваться);
          - возвращает строку (уже без обёрток), НЕ None;
          - при rate-limit бросает RateLimitError;
          - при сетевой/иной ошибке бросает исключение (Translator сам решит про ретрай).
        """
        raise NotImplementedError

    @property
    def name(self) -> str:
        """Человекочитаемое имя клиента (для логов)."""
        return self.__class__.__name__

    def close(self) -> None:
        """Освободить ресурсы (соединения, выгрузить модель). По умолчанию no-op."""
        return None
