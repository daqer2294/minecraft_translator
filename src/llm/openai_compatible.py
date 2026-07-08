# src/llm/openai_compatible.py
from __future__ import annotations

import http.client
import json
import socket
import ssl
import threading
import time
from typing import Any, List, Optional
from urllib.parse import urlsplit

import certifi

from .base import (
    LLMClient,
    Message,
    RateLimitError,
    LLMClientError,
    LLMTimeoutError,
    LLMCancelledError,
)


def _force_shutdown(conn) -> None:
    """Принудительно рвём сокет соединения — разблокирует зависший recv из другого
    потока (тот же приём, что и в загрузчике моделей). Безопасно при sock=None."""
    try:
        s = getattr(conn, "sock", None)
        if s is not None:
            s.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass


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

        body = json.dumps(payload).encode("utf-8")
        timeout = float(kwargs.get("timeout", self.timeout))
        cancel_event = kwargs.get("cancel_event")

        # Быстрый выход, если отмена запрошена ещё до старта запроса.
        if cancel_event is not None and cancel_event.is_set():
            raise LLMCancelledError("Запрос к модели отменён.")

        parts = urlsplit(url)
        scheme = parts.scheme or "http"
        host = parts.hostname or "localhost"
        port = parts.port or (443 if scheme == "https" else 80)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query

        if scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=self._ssl_ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)

        # Watchdog: разрывает сокет при отмене («Стоп») или если модель молчит
        # дольше таймаута. Это даёт РЕАЛЬНОЕ прерывание зависшего запроса, а не
        # ожидание следующей проверки на границе файла.
        wd_stop = threading.Event()
        flags = {"cancelled": False, "stalled": False}

        def _watchdog() -> None:
            deadline = time.time() + timeout + 5.0  # бэкстоп на случай, если socket-таймаут не сработает
            while not wd_stop.wait(0.2):
                if cancel_event is not None and cancel_event.is_set():
                    flags["cancelled"] = True
                    _force_shutdown(conn)
                    return
                if time.time() > deadline:
                    flags["stalled"] = True
                    _force_shutdown(conn)
                    return

        wd = threading.Thread(target=_watchdog, daemon=True)
        wd.start()
        try:
            conn.request("POST", path, body=body, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            raw = resp.read()
        except (socket.timeout, TimeoutError) as e:
            raise LLMTimeoutError(
                f"Таймаут запроса к модели ({timeout:.0f} c). Возможно, модель ещё "
                f"загружается в память или отвечает слишком медленно."
            ) from e
        except (ConnectionError, OSError, http.client.HTTPException, ValueError) as e:
            if flags["cancelled"] or (cancel_event is not None and cancel_event.is_set()):
                raise LLMCancelledError("Запрос к модели прерван пользователем.") from e
            if flags["stalled"]:
                raise LLMTimeoutError(
                    f"Модель не ответила за {timeout:.0f} c — запрос прерван."
                ) from e
            raise LLMClientError(f"Обрыв связи с моделью ({host}:{port}): {e}") from e
        finally:
            wd_stop.set()
            try:
                conn.close()
            except Exception:
                pass

        if status == 429:
            raise RateLimitError(f"429 rate limit: {raw[:300]!r}")
        if status >= 400:
            raise LLMClientError(f"HTTP {status}: {raw[:300]!r}")

        try:
            data = json.loads(raw.decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise LLMClientError(f"Unexpected response schema: {str(raw)[:300]}") from e
