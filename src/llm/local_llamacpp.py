# src/llm/local_llamacpp.py
from __future__ import annotations

import platform
from typing import Any, List, Optional

from .base import LLMClient, Message, LLMClientError


def _auto_n_gpu_layers() -> int:
    """
    Автовыбор числа слоёв, оффлоадимых на GPU, по платформе/железу.

      -1  → оффлоадить все слои (весь GPU);
       0  → чистый CPU.

    Логика:
      • macOS на Apple Silicon → -1 (Metal доступен, llama-cpp-python на mac
        собирается с Metal по умолчанию/через CMAKE_ARGS="-DGGML_METAL=on");
      • Windows/Linux с NVIDIA (по кэшу hardware_probe) → -1 — но это сработает,
        только если llama-cpp-python собран с CUDA; на CPU-сборке -1 безвреден
        (llama.cpp просто не найдёт GPU-бэкенд);
      • иначе → 0 (CPU).

    Значение можно переопределить явным n_gpu_layers в конструкторе или
    переменной окружения LLAMA_N_GPU_LAYERS.
    """
    import os

    env = os.environ.get("LLAMA_N_GPU_LAYERS")
    if env is not None:
        try:
            return int(env)
        except ValueError:
            pass

    system = platform.system()
    machine = platform.machine().lower()

    # Apple Silicon → Metal
    if system == "Darwin" and machine in ("arm64", "aarch64"):
        return -1

    # NVIDIA (только по уже сохранённой пробе — без запуска nvidia-smi здесь)
    try:
        from . import hardware_probe as hp
        info = hp.load_cached()
        if (
            info
            and info.gpu_available
            and "nvidia" in (info.gpu_name or "").lower()
            and info.gpu_vram_mb >= 2000
        ):
            return -1
    except Exception:
        pass

    return 0

# ЗАГЛУШКА-РЕАЛИЗАЦИЯ (ШАГ бенчмарка выберет конкретную модель/веса).
#
# Поддерживаются два способа локального инференса — выбирается конструктором:
#
#  1) SERVER MODE (рекомендуется для этого пайплайна):
#     запускаем `llama-server` (из llama.cpp) с GGUF-моделью, он даёт
#     OpenAI-совместимый эндпоинт. Тогда этот клиент просто делегирует
#     в OpenAICompatibleClient(server_url).
#     Плюсы: модель грузится один раз в отдельном процессе; сервер сам
#     обслуживает параллельные запросы (у нас ThreadPoolExecutor на 6 воркеров
#     в mirrorer.py) без GIL-проблем и без повторной загрузки весов.
#     Запуск примерно так:
#         ./llama-server -m model.gguf -c 4096 --host 127.0.0.1 --port 8080
#     и local_server_url = "http://127.0.0.1:8080"
#
#  2) IN-PROCESS MODE (llama-cpp-python):
#     грузим модель прямо в процесс через `llama_cpp.Llama`. Проще в дистрибуции
#     (одно приложение), но: тяжёлая загрузка весов, инференс под GIL —
#     параллелизм по воркерам эффекта почти не даёт, лучше выставить
#     MAX_WORKERS_FILES=1. Подходит для одиночной машины/CLI.
#
# STEP 4: сама модель/веса и оптимальные n_ctx/n_gpu_layers подбираются на
# этапе бенчмарка — здесь только каркас и корректный интерфейс chat().


class LocalLlamaCppClient(LLMClient):
    def __init__(
        self,
        model_path: str = "",
        *,
        server_url: Optional[str] = None,
        model: str = "local-gguf",
        n_ctx: int = 4096,
        n_gpu_layers: Optional[int] = None,   # None → автоопределение по платформе
        n_threads: Optional[int] = None,
        chat_format: Optional[str] = None,
        default_temperature: float = 0.0,
        verbose: bool = False,
        prompt_cache: bool = True,
        **llama_kwargs: Any,
    ):
        self.model_path = model_path or ""
        self.server_url = server_url or None
        self.model = model
        self.n_ctx = int(n_ctx)
        # платформенный автовыбор, если явно не задано (Metal на Apple Silicon,
        # CUDA на NVIDIA-сборке, иначе CPU)
        self.n_gpu_layers = _auto_n_gpu_layers() if n_gpu_layers is None else int(n_gpu_layers)
        self.n_threads = n_threads
        self.chat_format = chat_format
        self.default_temperature = float(default_temperature)
        self.verbose = bool(verbose)
        # STEP 4 — prefix caching. В server mode просим llama-server кэшировать
        # промпт между запросами.
        self.prompt_cache = bool(prompt_cache)
        self._llama_kwargs = llama_kwargs

        # ленивая инициализация
        self._llm = None          # экземпляр llama_cpp.Llama (in-process)
        self._server = None       # OpenAICompatibleClient (server mode)

    @property
    def name(self) -> str:
        if self.server_url:
            return f"llamacpp-server({self.model}@{self.server_url})"
        return f"llamacpp-inproc({self.model_path or 'NO_MODEL'})"

    # ---------- backends ----------

    def _ensure_server(self):
        if self._server is None:
            # Локальный llama-server — OpenAI-совместимый, переиспользуем клиент.
            from .openai_compatible import OpenAICompatibleClient

            # ---- Prefix / prompt caching (STEP 4) ----
            # llama-server (llama.cpp) поддерживает переиспользование KV-кэша:
            #   1) поле запроса "cache_prompt": true — сервер сохраняет KV-кэш
            #      промпта в слоте и переиспользует общий префикс на следующем
            #      запросе (наш system-prompt идентичен между запросами — см.
            #      константы в translators.py, это и есть общий префикс);
            #   2) флаг сервера --cache-reuse N (напр. 256) включает частичное
            #      переиспользование префикса даже при небольших расхождениях.
            # Источник: llama.cpp server README (examples/server) —
            #   параметр запроса `cache_prompt` и опция `--cache-reuse`.
            # Рекомендуемый запуск сервера:
            #   ./llama-server -m model.gguf -c 4096 --cache-reuse 256 \
            #                  --host 127.0.0.1 --port 8080
            extra_body = {"cache_prompt": True} if self.prompt_cache else None

            self._server = OpenAICompatibleClient(
                base_url=self.server_url,
                api_key="",  # локальному серверу ключ не нужен
                model=self.model,
                default_temperature=self.default_temperature,
                extra_body=extra_body,
            )
        return self._server

    def _ensure_llm(self):
        """Ленивая загрузка in-process модели через llama-cpp-python."""
        if self._llm is not None:
            return self._llm

        if not self.model_path:
            raise LLMClientError(
                "LocalLlamaCppClient: не задан local_model_path (и не указан "
                "local_server_url). Модель выбирается на этапе бенчмарка (STEP 4). "
                "Задайте путь к .gguf в ProviderConfig.local_model_path или "
                "поднимите llama-server и укажите local_server_url."
            )

        try:
            from llama_cpp import Llama  # тяжёлый импорт — только при реальном использовании
        except Exception as e:  # pragma: no cover - зависит от окружения
            raise LLMClientError(
                "llama-cpp-python не установлен. Установите пакет "
                "(pip install llama-cpp-python) или используйте server mode "
                f"(local_server_url). Исходная ошибка: {e}"
            ) from e

        import os
        if not os.path.exists(self.model_path):
            raise LLMClientError(f"GGUF-модель не найдена: {self.model_path}")

        kwargs = dict(
            model_path=self.model_path,
            n_ctx=self.n_ctx,
            n_gpu_layers=self.n_gpu_layers,
            verbose=self.verbose,
        )
        if self.n_threads is not None:
            kwargs["n_threads"] = self.n_threads
        if self.chat_format is not None:
            kwargs["chat_format"] = self.chat_format
        kwargs.update(self._llama_kwargs)

        self._llm = Llama(**kwargs)
        return self._llm

    # ---------- интерфейс ----------

    def chat(self, messages: List[Message], **kwargs: Any) -> str:
        # server mode → делегируем OpenAI-совместимому клиенту
        if self.server_url:
            return self._ensure_server().chat(messages, **kwargs)

        # in-process mode
        llm = self._ensure_llm()
        params = dict(
            messages=messages,
            temperature=kwargs.get("temperature", self.default_temperature),
        )
        if kwargs.get("max_tokens") is not None:
            params["max_tokens"] = kwargs["max_tokens"]
        if kwargs.get("top_p") is not None:
            params["top_p"] = kwargs["top_p"]
        if kwargs.get("response_format") is not None:
            params["response_format"] = kwargs["response_format"]

        resp = llm.create_chat_completion(**params)
        try:
            return resp["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as e:
            raise LLMClientError(f"Unexpected llama.cpp response: {str(resp)[:300]}") from e

    def close(self) -> None:
        # llama_cpp.Llama освобождается сборщиком мусора; явных ресурсов не держим.
        self._llm = None
