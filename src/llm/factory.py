# src/llm/factory.py
from __future__ import annotations

import os
import threading
from typing import Callable, List, Optional, Tuple, TYPE_CHECKING

from .base import LLMClient
from .openai_compatible import OpenAICompatibleClient
from .local_llamacpp import LocalLlamaCppClient
from . import model_registry as registry
from . import model_downloader as downloader
from . import ollama_probe

if TYPE_CHECKING:
    from ..config import ProviderConfig


def _make_external(cfg) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url=cfg.external_base_url,
        api_key=cfg.external_api_key,
        model=cfg.external_model,
        # Часть моделей внешнего API отвергает max_tokens с HTTP 400.
        send_max_tokens=False,
    )


def _make_ollama(cfg) -> OpenAICompatibleClient:
    """
    Ollama = тот же OpenAICompatibleClient, только с base_url локальной Ollama и
    без ключа. Клиент сам добавит /v1/chat/completions → это OpenAI-эндпоинт
    Ollama. Логику НЕ дублируем.
    """
    return OpenAICompatibleClient(
        base_url=cfg.ollama_base_url,
        api_key="",                 # Ollama не требует ключа
        model=cfg.ollama_model,
    )


def _resolve_spec(cfg, tier: str):
    """Спека модели для тира: явный *_model_id из конфига или дефолт реестра."""
    mid = cfg.light_model_id if tier == "light" else cfg.standard_model_id
    spec = registry.get_by_id(mid) if mid else None
    return spec or registry.default_for_tier(tier)


def _make_local_light(cfg) -> LocalLlamaCppClient:
    """Лёгкий локальный клиент (для простых строк — всегда)."""
    # 1) явный путь к .gguf (обратная совместимость / ручной оверрайд)
    if cfg.local_model_path:
        return LocalLlamaCppClient(
            model_path=cfg.local_model_path,
            server_url=(cfg.local_server_url or None),
            model=cfg.local_model,
            prompt_cache=cfg.prompt_cache,
        )
    # 2) server mode: llama-server уже обслуживает модель
    if cfg.local_server_url:
        spec = _resolve_spec(cfg, "light")
        return LocalLlamaCppClient(
            server_url=cfg.local_server_url,
            model=(spec.id if spec else cfg.local_model),
            prompt_cache=cfg.prompt_cache,
        )
    # 3) in-process по реестру: путь в кэше приложения (GUI гарантирует скачивание)
    spec = _resolve_spec(cfg, "light")
    path = downloader.local_model_path(spec) if spec else (cfg.local_model_path or "")
    return LocalLlamaCppClient(
        model_path=path,
        model=(spec.id if spec else cfg.local_model),
        prompt_cache=cfg.prompt_cache,
        n_gpu_layers=fast_gpu_layers(cfg),
    )


GPU_PROFILES = {
    "auto": "Как обычно: основная модель на видеокарте, если она есть",
    "fast_cpu": "Основная модель на процессоре, видеокарта под сильную; длинные тексты идут ей одновременно",
}


def fast_gpu_layers(cfg) -> Optional[int]:
    """Слои основной модели на GPU. None: автоопределение, как раньше; 0: только процессор."""
    return 0 if getattr(cfg, "gpu_profile", "auto") == "fast_cpu" else None


def _make_local_standard(cfg) -> Optional[LocalLlamaCppClient]:
    """
    Мощный локальный клиент для сложных строк — ТОЛЬКО если он реально доступен:
      - в server mode отдельного standard нет (один сервер = одна модель) → None;
      - собираем только на standard-тире или при явном standard_model_id;
      - если файл не скачан → None (сложные строки уйдут на light с пометкой).
    """
    if cfg.local_server_url:
        return None
    if not (cfg.tier == "standard" or cfg.standard_model_id):
        return None
    spec = _resolve_spec(cfg, "standard")
    if spec is None:
        return None
    path = downloader.local_model_path(spec)
    if not os.path.exists(path):
        return None
    return LocalLlamaCppClient(
        model_path=path,
        model=spec.id,
        prompt_cache=cfg.prompt_cache,
    )


def _find_ollama_model(want: str, names) -> Optional[str]:
    """Точное имя, имя без тега или начало тега: «llama3.1:8b» подходит к «llama3.1:8b-instruct-q4_K_M»."""
    for name in names:
        if name == want or name.split(":")[0] == want or name.startswith(want + "-"):
            return name
    return None


def build_strong_client(cfg: "ProviderConfig") -> Tuple[Optional[LLMClient], str]:
    """
    Клиент для второго прохода по браку основной модели и причина, если его нет.
    Порядок: выбранная модель в Ollama, затем скачанный Standard. Ничего не скачивает.
    Внешний API не используется: он платный и не выбран пользователем для этого.
    """
    mode = (getattr(cfg, "mode", "local") or "local").lower()
    want = (getattr(cfg, "strong_model", "") or "").strip()
    if want.lower() == "off":
        return None, "Второй проход отключён настройкой strong_model."
    if mode == "external":
        return None, "Основная модель внешняя, сильнее локально нет."

    url = cfg.ollama_base_url or ollama_probe.OLLAMA_DEFAULT_URL
    if want:
        status = ollama_probe.probe_ollama(url)
        name = _find_ollama_model(want, [m.name for m in status.models])
        if name and mode == "ollama" and name == cfg.ollama_model:
            return None, f"Основная модель уже {name}."
        if name:
            return OpenAICompatibleClient(base_url=url, api_key="", model=name), ""

    spec = None if cfg.local_server_url else _resolve_spec(cfg, "standard")
    if spec is not None and downloader.is_downloaded(spec):
        return LocalLlamaCppClient(
            model_path=downloader.local_model_path(spec), model=spec.id, prompt_cache=cfg.prompt_cache,
        ), ""

    return None, (
        f"Сильной модели нет: она улучшит результат. Установите «ollama pull {want}» "
        "или скачайте модель Standard в приложении."
    )


def shared_strong_client(cfg: "ProviderConfig") -> Callable[[], Tuple[Optional[LLMClient], str]]:
    """Один клиент на прогон: длинные тексты и второй проход не должны загружать модель дважды."""
    lock = threading.Lock()
    built: List[Tuple[Optional[LLMClient], str]] = []

    def get() -> Tuple[Optional[LLMClient], str]:
        with lock:
            if not built:
                built.append(build_strong_client(cfg))
            return built[0]

    return get


def build_clients(cfg: "ProviderConfig") -> Tuple[LLMClient, Optional[LLMClient]]:
    """
    Собирает (light_client, complex_client) под двумерный роутинг (STEP 5).

    light_client   — простые строки (всегда), complex_client — сложные (опц.).

    - "external": оба через внешний API. light — внешний клиент; complex — тот
                  же внешний клиент (он и есть «мощная» модель, поэтому сложные
                  строки уходят на него БЕЗ пометки о деградации качества).
    - "local"   : light = локальная лёгкая; complex = локальная мощная, если
                  тир standard и модель скачана, иначе None (fallback на light).
    - "hybrid"  : light = локальная лёгкая; complex = внешний API.

    Режим по умолчанию — "local".
    """
    mode = (getattr(cfg, "mode", "local") or "local").lower()

    if mode == "external":
        ext = _make_external(cfg)
        # complex = тот же внешний клиент: сложные строки обслуживаются мощной
        # моделью, разовая пометка про «light-fallback» не нужна.
        return ext, ext

    if mode == "ollama":
        # Один клиент Ollama на всё (простые и сложные строки).
        oll = _make_ollama(cfg)
        return oll, oll

    light = _make_local_light(cfg)

    if mode == "hybrid":
        return light, _make_external(cfg)

    # local (default) + любой неизвестный режим трактуем как local
    return light, _make_local_standard(cfg)
