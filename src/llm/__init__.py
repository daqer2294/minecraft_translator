# src/llm/__init__.py
from __future__ import annotations

from .base import LLMClient, Message, LLMClientError, RateLimitError
from .openai_compatible import OpenAICompatibleClient
from .local_llamacpp import LocalLlamaCppClient
from .factory import build_clients
from . import model_registry, hardware_probe, model_downloader
from .model_registry import ModelSpec
from .hardware_probe import HardwareInfo
from .model_downloader import DownloadError

__all__ = [
    "LLMClient",
    "Message",
    "LLMClientError",
    "RateLimitError",
    "OpenAICompatibleClient",
    "LocalLlamaCppClient",
    "build_clients",
    "model_registry",
    "hardware_probe",
    "model_downloader",
    "ModelSpec",
    "HardwareInfo",
    "DownloadError",
]
