# src/utils/prompt_leak.py
"""
Утечка промпта: модель вместо перевода пересказывает наши инструкции.
«Токены like [[0]] и [[1]] stand для маркетинг. Оставьте каждый из них...»

Разметка в таком ответе цела, поэтому обычные проверки его пропускают. Детектор строится
из самих текстов промптов, поэтому новая формулировка промпта сразу попадает в проверку.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Set, Tuple

from .markup import MARKUP_RE

_WORD_RE = re.compile(r"[^\W\d_]+")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+")
_CYRILLIC_RE = re.compile("[Ѐ-ԯ]")
_TEMPLATE_FIELD_RE = re.compile(r"__\w+__")

# Подряд идущие слова: у кириллицы длиннее, «каждый из них» бывает и в обычном тексте
_LATIN_NGRAM = 3
_CYRILLIC_NGRAM = 4
# Сколько разных английских слов из промпта, которых нет в источнике, считается утечкой
_FOREIGN_WORDS_LIMIT = 2
# Эти слова модель законно вставляет в русский текст сама
_ORDINARY_WORDS = {"minecraft", "json", "id", "ids", "mod", "mods", "modid", "modpacks", "item", "text", "e", "g", "etc"}


def _words(text: str) -> List[str]:
    return [w.lower() for w in _WORD_RE.findall(MARKUP_RE.sub(" ", text))]


def _ngrams(words: List[str], size: int) -> Set[Tuple[str, ...]]:
    return {tuple(words[i:i + size]) for i in range(len(words) - size + 1)}


def _latin_only(gram: Tuple[str, ...]) -> bool:
    return all(_LATIN_WORD_RE.fullmatch(w) for w in gram)


class PromptLeakDetector:
    def __init__(self, prompt_texts: Iterable[str]):
        self._phrases: Set[Tuple[str, ...]] = set()
        self._vocabulary: Set[str] = set()
        for text in prompt_texts:
            words = _words(_TEMPLATE_FIELD_RE.sub(" ", text))
            self._phrases |= {g for g in _ngrams(words, _LATIN_NGRAM) if _latin_only(g)}
            self._phrases |= {g for g in _ngrams(words, _CYRILLIC_NGRAM) if not _latin_only(g)}
            self._vocabulary |= {w for w in words if _LATIN_WORD_RE.fullmatch(w)} - _ORDINARY_WORDS

    @property
    def vocabulary(self) -> Set[str]:
        return self._vocabulary

    def explain(self, answer: str, source: str) -> str:
        """Что в ответе взято из промпта. Пустая строка, если утечки нет. Источник всегда в счёт не идёт."""
        words, source_words = _words(answer), _words(source)
        source_phrases = _ngrams(source_words, _LATIN_NGRAM) | _ngrams(source_words, _CYRILLIC_NGRAM)
        for size in (_LATIN_NGRAM, _CYRILLIC_NGRAM):
            for gram in _ngrams(words, size):
                if gram in self._phrases and gram not in source_phrases:
                    return "фраза промпта: " + " ".join(gram)
        # Разрозненные слова считаются только в кириллическом ответе: в немецком «stand» обычное слово
        if _CYRILLIC_RE.search(answer):
            foreign = sorted((set(words) & self._vocabulary) - set(source_words))
            if len(foreign) >= _FOREIGN_WORDS_LIMIT:
                return "слова промпта: " + ", ".join(foreign[:5])
        return ""
