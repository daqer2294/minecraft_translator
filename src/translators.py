# src/translators.py
from __future__ import annotations
import json
import os
import re
import time
import urllib.request
import urllib.error
import ssl
from typing import Dict

import certifi  # убедись, что установлен

from .utils.cache import TranslationCache
from . import config


# --- плейсхолдеры/токены, которые нельзя ломать ---
RE_PLACEHOLDER = re.compile(
    r"%(?:\d+\$)?[sdifx]|"      # %s, %d, %1$s
    r"\{[\w\.]+\}"              # {count}, {0}, {player.name}
)
RE_NAMESPACE = re.compile(r"[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+")  # modid:item
RE_LATIN = re.compile(r"[A-Za-z]")


def _extract_tokens(s: str) -> tuple[str, ...]:
    if not isinstance(s, str) or not s:
        return tuple()
    toks = []
    toks.extend(RE_PLACEHOLDER.findall(s))
    toks.extend(RE_NAMESPACE.findall(s))
    return tuple(sorted(toks))


class Translator:
    """
    Переводчик с кэшем и строгой валидацией плейсхолдеров.
    Если набор плейсхолдеров до/после отличается — перевод отклоняется (fallback к оригиналу).
    """

    def __init__(self, provider: str, model: str, api_key: str, cache: TranslationCache, strict: bool = True):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.cache = cache
        self.strict = strict
        self.cache.load()

        # SSL контекст с системными сертификатами (лечит CERTIFICATE_VERIFY_FAILED)
        self._ssl_ctx = ssl.create_default_context(cafile=certifi.where())

    # ----------------- основной метод -----------------
    def translate(self, text: str, target_lang: str = "ru_ru") -> str:
        """Перевод одной строки, с кэшем и валидацией."""
        if not text:
            return text

        # кэш по исходнику (важно: кэш именно по оригинальному тексту)
        cached = self.cache.get(text)
        if cached:
            return cached

        src_tokens = _extract_tokens(text)
        translated = self._translate_via_openai(text, target_lang).strip()

        # валидация: плейсхолдеры до/после должны совпадать
        dst_tokens = _extract_tokens(translated)
        if src_tokens != dst_tokens:
            print(f"[VALIDATE] placeholders mismatch → fallback\n  src:{src_tokens}\n  dst:{dst_tokens}\n  text:{text!r}\n  got :{translated!r}")
            if self.strict:
                translated = text  # отклоняем перевод

        # сохраняем (в любом случае фиксируем итог, чтобы не дёргать API повторно)
        self.cache.put(text, translated)
        self.cache.save()
        return translated

    # ----------------- реализация openai -----------------
    def _translate_via_openai(self, text: str, target_lang: str) -> str:
        """
        Запрос к OpenAI через HTTP, строгие инструкции по плейсхолдерам.
        Возвращает переведённую строку или исходник (при ошибке сети).
        """
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a professional localization engine for Minecraft mods.\n"
                        "Translate the following English text to Russian.\n"
                        "Do NOT translate or alter placeholders (e.g., %s, %1$s, {count}, {0}) or namespaced IDs like modid:item.\n"
                        "Keep capitalization/punctuation natural. Return ONLY the translated text."
                    ),
                },
                {"role": "user", "content": text},
            ],
        }

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60, context=self._ssl_ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"].strip()
            return content
        except urllib.error.HTTPError as e:
            print(f"[HTTPError] {e.code} {e.reason}")
        except urllib.error.URLError as e:
            print(f"[URLError] {e.reason}")
        except Exception as e:
            print(f"[TranslateError] {e}")
        time.sleep(config.RATE_LIMIT_SLEEP)
        return text
