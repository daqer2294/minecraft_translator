# src/llm/openai_compatible.py
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any, List, Optional

import certifi

from .base import LLMClient, Message, RateLimitError, LLMClientError


class OpenAICompatibleClient(LLMClient):
    """
    Клиент для любого OpenAI-совместимого Chat Completions API.

    Это ровно та же сетевая логика, что раньше была зашита в Translator
    (urllib → POST {base_url}/v1/chat/completions), но теперь:
      - base_url конфигурируется (закрывает R-4 / Q-1): OpenAI, DeepSeek, Qwen,
        llama.cpp server, LM Studio, Ollama (/v1), vLLM, groq, together...
      - пустой api_key допустим (локальные серверы обычно не требуют ключ).
    """

    def __init__(
        self,
        base_url: str = "https://api.openai.com",
        api_key: str = "",
        model: str = "gpt-4o-mini",
        *,
        timeout: float = 90.0,
        organization: Optional[str] = None,
        default_temperature: float = 0.0,
        extra_body: Optional[dict] = None,
    ):
        self.base_url = (base_url or "https://api.openai.com").rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self.timeout = float(timeout)
        self.organization = organization
        self.default_temperature = float(default_temperature)
        # Доп. поля тела запроса, добавляемые к каждому вызову. Используется, в
        # частности, для prefix/prompt caching у llama-server ({"cache_prompt": true}).
        self.extra_body = dict(extra_body or {})
        self._ssl_ctx = ssl.create_default_context(cafile=certifi.where())

    @property
    def name(self) -> str:
        return f"openai-compatible({self.model}@{self.base_url})"

    def chat(self, messages: List[Message], **kwargs: Any) -> str:
        url = f"{self.base_url}/v1/chat/completions"

        payload = {
            "model": kwargs.get("model", self.model),
            "temperature": kwargs.get("temperature", self.default_temperature),
            "messages": messages,
        }
        # опциональные параметры пробрасываем только если заданы
        if kwargs.get("max_tokens") is not None:
            payload["max_tokens"] = kwargs["max_tokens"]
        if kwargs.get("top_p") is not None:
            payload["top_p"] = kwargs["top_p"]
        if kwargs.get("response_format") is not None:
            payload["response_format"] = kwargs["response_format"]

        # доп. поля тела: сначала клиентские (self.extra_body), затем per-call.
        # НЕ перетираем базовые ключи (model/messages/temperature).
        _reserved = {"model", "messages", "temperature"}
        for extra in (self.extra_body, kwargs.get("extra_body")):
            if extra:
                for k, v in extra.items():
                    if k not in _reserved:
                        payload[k] = v

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.organization:
            headers["OpenAI-Organization"] = self.organization

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        timeout = float(kwargs.get("timeout", self.timeout))
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self._ssl_ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            if e.code == 429:
                raise RateLimitError(f"429 rate limit: {body[:300]}") from e
            raise LLMClientError(f"HTTP {e.code}: {body[:300]}") from e

        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as e:
            raise LLMClientError(f"Unexpected response schema: {str(data)[:300]}") from e
