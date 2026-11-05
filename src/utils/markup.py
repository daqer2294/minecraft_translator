# src/utils/markup.py
"""
Разметка Minecraft: плейсхолдеры, коды форматирования, макросы, ID.

Модель её теряет и переводит: на реальных строках плейсхолдеры выживали в 69-90 %
случаев, коды в 23-89 %, переносы в 39-61 %. Поэтому разметка не отправляется вовсе.
Токены по краям строки снимаются, внутри заменяются на [[N]] и возвращаются после ответа.
То же для выражений кода: вызовов, camelCase и snake_case.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# Выражения кода. Модель переводит их как текст: «cubicbezier(k, x)» → «кубическийбе́зье(к, х)».
# Может стоять сразу после кода цвета, \\n или %s, поэтому такое соседство разрешено.
_CODE_START = r"(?:(?<![\w.$])|(?<=[§&][0-9a-fk-orA-FK-OR])|(?<=\\n)|(?<=%[sd]))"
_CODE_ARG = r"""[\w.$"'*\[\]|]+(?:\s*(?:=|\?:|:)\s*[\w.$"'\-\[\]|]*)?(?:\.\.\.)?"""
# name(arg, ...): без пробела перед скобкой, имя с маленькой буквы или с точкой, аргумент
# с буквы. Не код: «Moderate (1.5)», «Sword(Diamond)», «Size(1.5)», «item(s)», «entity(ies)».
_CODE_CALL = (
    _CODE_START + r"(?:[a-z_][\w$]*|[A-Za-z_][\w$]*(?:\.[\w$]+)+)"
    r"\((?!(?:s|es|ies|ed|d|ing|er|or|r|n|th|st|nd|rd)\))"
    r"""(?:\s*[A-Za-z_"'][\w.$"'*\[\]|]*(?:\s*(?:=|\?:|:)\s*[\w.$"'\-\[\]|]*)?(?:\.\.\.)?"""
    r"(?:\s*,\s*" + _CODE_ARG + r")*\s*)?\)"
)
_CODE_CAMEL = _CODE_START + r"[a-z][a-z0-9]*(?:[A-Z][a-z0-9]*)+(?![\w$])"
# Только единый регистр: «Light_gray» и «Diagonal_Right» в названиях это текст.
_CODE_SNAKE = _CODE_START + r"(?:[a-z][a-z0-9]*(?:_[a-z0-9]+)+|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)(?![\w$])"

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
    + "|" + _CODE_CALL + "|" + _CODE_CAMEL + "|" + _CODE_SNAKE
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

# Тот же смысл по-русски: модель пересказывает пояснение на языке перевода. Только для
# поиска утечки в prompt_leak, в запрос не отправляется. Меняется вместе с MASK_PROMPT_NOTE.
MASK_PROMPT_NOTE_RU = (
    "Токены вроде [[0]] и [[1]] обозначают разметку. Оставьте каждый из них ровно один раз, "
    "без изменений, в естественном положении."
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


# Вступление модели перед переводом и слово в источнике, при котором такое начало законно.
# Голые «Хорошо», «Вот», «Пожалуйста» сюда не входят: это обычные переводы.
_INTRO_PHRASES = (
    (re.compile(r"хорошо\s*[,!:]\s*вот\b"), None),
    (re.compile(r"вот\s+(?:ваш\s+|сам\s+)?перевод\b"), None),
    (re.compile(r"конечно\s*[,!:]"), r"course|sure|certainly|naturally|definitely"),
    (re.compile(r"перевод\s*:"), r"translat"),
    (re.compile(r"переведено\s*:"), r"translat"),
    (re.compile(r"here(?:\s+is|'s|’s)\b"), r"\bhere(?:\s+is|'s|’s)"),
    (re.compile(r"sure\s*[,!:]"), r"\bsure\b"),
    (re.compile(r"translation\s*:"), r"translation"),
    (re.compile(r"translated\s*:"), r"translated"),
)
_RAW_MASK_RE = re.compile(r"\[\[.*?\]\]", re.DOTALL)


def has_intro_or_raw_mask(source: str, answer: str) -> bool:
    """Ответ начинается со вступления или в нём осталось [[...]], которого нет в источнике."""
    if _RAW_MASK_RE.search(answer) and not _RAW_MASK_RE.search(source):
        return True
    start = re.sub(r"^\W+", "", answer).lower()
    lowered = source.lower()
    return any(
        phrase.match(start) and not (allowed and re.search(allowed, lowered))
        for phrase, allowed in _INTRO_PHRASES
    )


# Английское слово, оставшееся в русском ответе: «Цветные crayones», «для infliction damage».
# Слово должно быть не короче этого числа букв: mB, HP и подобные не считаются.
FOREIGN_WORD_MIN_LETTERS = 4
_LATIN_WORD_RE = re.compile("[A-Za-z\u00c0-\u024f]+")
_RUSSIAN_ENDING_RE = re.compile("['\u2019][\u0400-\u052f]")


def foreign_latin_words(source: str, answer: str) -> List[str]:
    """
    Латинские слова из ответа с кириллицей, которых нет в источнике (регистр не важен).
    Разметка и код не считаются. Не считается и термин, оформленный по-русски:
    с окончанием через апостроф («MacBook'ах») или целиком в скобках («(mipmapping)»).
    """
    if not _CYRILLIC_RE.search(answer):
        return []
    known = {w.lower() for w in _LATIN_WORD_RE.findall(MARKUP_RE.sub(" ", source))}
    text = MARKUP_RE.sub(" ", answer)
    found = []
    for match in _LATIN_WORD_RE.finditer(text):
        word = match.group(0)
        if len(word) < FOREIGN_WORD_MIN_LETTERS or word.lower() in known:
            continue
        before, after = text[max(0, match.start() - 1):match.start()], text[match.end():match.end() + 2]
        if _RUSSIAN_ENDING_RE.match(after) or (before == "(" and after[:1] == ")"):
            continue
        found.append(word)
    return found


# Выдуманный ответ на короткую строку: «PKMNCC» → «Покемонов не существует в Minecraft.»
# Числа проверены на всём кэше: они ловят все известные случаи без ложных срабатываний.
HALLUCINATION_MAX_SOURCE_WORDS = 6
HALLUCINATION_MIN_ANSWER_WORDS = 5
HALLUCINATION_LENGTH_RATIO = 3


def looks_hallucinated(source: str, answer: str) -> bool:
    """Источник короткий и без разметки, а ответ намного длиннее него. Длинные слова не в счёт: «Custom» → «Персонализированный»."""
    if MARKUP_RE.search(source):
        return False
    return (
        len(_WORD_RE.findall(source)) <= HALLUCINATION_MAX_SOURCE_WORDS
        and len(_WORD_RE.findall(MARKUP_RE.sub(" ", answer))) >= HALLUCINATION_MIN_ANSWER_WORDS
        and len(answer.strip()) > HALLUCINATION_LENGTH_RATIO * len(source.strip())
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
