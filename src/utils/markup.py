# src/utils/markup.py
"""
Разметка Minecraft: плейсхолдеры, коды форматирования, макросы, ID.

Модель её теряет и переводит: на реальных строках плейсхолдеры выживали в 69-90 %
случаев, коды в 23-89 %, переносы в 39-61 %. Поэтому разметка не отправляется вовсе.
Токены по краям строки снимаются, внутри заменяются на [[N]] и возвращаются после ответа.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

MARKUP_RE = re.compile(
    r"%(?:\d+\$)?-?\d*(?:\.\d+)?[sd]"          # %s, %1$s, %02d
    r"|%(?:\d+\$)?-?\d*(?:\.\d+)?[ifxbc](?![A-Za-z])"  # %f, %x; не «10%off»
    r"|%%"
    r"|\{[^{}\n]{0,120}\}"                     # {0}, {count}, {@pagebreak}, {image:a:b width:100}
    r"|[§&][0-9a-fk-orA-FK-OR]"                # §a, &6
    r"|\$\([^)\n]{0,80}\)"                     # $(item) в Patchouli
    r"|<[^<>\n]{1,40}>"                        # <red>, <br>
    r"|\[\[\s*\d+\s*\]\]"                      # наш же токен в исходнике
    r"|\\n"                                    # \n двумя символами, как в SNBT
    r"|\n"
    r"|(?<![A-Za-z0-9_.\-])[a-z0-9_.\-]*[a-z][a-z0-9_.\-]*:[a-z0-9_/.\-]*[a-z_/][a-z0-9_/.\-]*"  # modid:item
)
_MASK_RE = re.compile(r"\[\[\s*(\d+)\s*\]\]")
_FORMAT_CODE_RE = re.compile(r"[§&][0-9a-fk-orA-FK-OR]")
_WORD_RE = re.compile(r"[^\W\d_]+")
_CYRILLIC_RE = re.compile("[\u0400-\u052f]")
_LATIN_RE = re.compile("[A-Za-z\u00c0-\u024f]")

MASK_PROMPT_NOTE = (
    "\nTokens like [[0]] and [[1]] stand for markup. Keep every one of them exactly once, "
    "unchanged, in the natural position."
)


@dataclass
class MaskedText:
    prefix: str = ""
    core: str = ""
    suffix: str = ""
    tokens: List[str] = field(default_factory=list)


def has_mixed_script_word(text: str) -> bool:
    """
    Есть слово из смеси кириллицы и латиницы: «Мinecraft», «Сила пaddle». Модель так пишет,
    когда начинает переводить и бросает. Разметка не считается, части через дефис, слэш,
    апостроф и цифры проверяются отдельно: «X-образный» и «Minecraft'а» в порядке.
    """
    return any(
        _CYRILLIC_RE.search(word) and _LATIN_RE.search(word)
        for word in _WORD_RE.findall(MARKUP_RE.sub(" ", text))
    )


def mask(text: str) -> MaskedText:
    """Края строки снимаются целиком, разметка внутри заменяется на [[N]]."""
    found = list(MARKUP_RE.finditer(text))
    start, end = 0, len(text)

    # Края: токены и пробелы подряд от начала и от конца
    for m in found:
        if text[start:m.start()].strip():
            break
        start = m.end()
    for m in reversed(found):
        if m.start() < start or text[m.end():end].strip():
            break
        end = m.start()
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1

    result = MaskedText(prefix=text[:start], suffix=text[end:])
    parts = []
    position = start
    for m in found:
        if m.start() < start or m.end() > end:
            continue
        parts.append(text[position:m.start()])
        parts.append(f"[[{len(result.tokens)}]]")
        result.tokens.append(m.group(0))
        position = m.end()
    parts.append(text[position:end])
    result.core = "".join(parts)
    return result


def unmask(answer: str, masked: MaskedText) -> Optional[str]:
    """Собирает строку обратно. None, если модель потеряла или размножила токены."""
    answer = answer.strip()
    if not answer:
        return None
    numbers = sorted(int(n) for n in _MASK_RE.findall(answer))
    if numbers != list(range(len(masked.tokens))):
        return None
    # Модель любит ставить пробел после токена цвета: «[[0]] Текст» при «[[0]]Текст»
    no_space_after = {
        str(i) for i, t in enumerate(masked.tokens)
        if _FORMAT_CODE_RE.fullmatch(t) and f"[[{i}]] " not in masked.core
    }

    def restore(match: re.Match) -> str:
        return masked.tokens[int(match.group(1))]

    answer = re.sub(
        r"\[\[\s*(\d+)\s*\]\]\s+",
        lambda m: f"[[{m.group(1)}]]" if m.group(1) in no_space_after else m.group(0),
        answer,
    )
    return masked.prefix + _MASK_RE.sub(restore, answer) + masked.suffix
