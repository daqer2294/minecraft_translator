# src/processors/__init__.py
"""Lightweight processors package: no heavy imports at import time.

Импорты конкретных подмодулей делаем точечно там, где они реально нужны.
"""

__all__ = [
    "lang_json",
    "generic_json",
    "kubejs_js",
    "jar_lang",
    "ftb_snbt",
]
