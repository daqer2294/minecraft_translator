# src/llm/factory.py
from __future__ import annotations

import os
from typing import Optional, Tuple, TYPE_CHECKING

from .base import LLMClient
from .openai_compatible import OpenAICompatibleClient
from .local_llamacpp import LocalLlamaCppClient
from . import model_registry as registry
from . import model_downloader as downloader

if TYPE_CHECKING:
    from ..config import ProviderConfig


def _make_external(cfg) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url=cfg.external_base_url,
        api_key=cfg.external_api_key,
        model=cfg.external_model,
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
    )


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

    light = _make_local_light(cfg)

    if mode == "hybrid":
        return light, _make_external(cfg)

    # local (default) + любой неизвестный режим трактуем как local
    return light, _make_local_standard(cfg)
