# src/utils/markup.py
"""
Разметка Minecraft: плейсхолдеры, коды форматирования, макросы, ID.

Модель её теряет и переводит: на реальных строках плейсхолдеры выживали в 69-90 %
случаев, коды в 23-89 %, переносы в 39-61 %. Поэтому разметка не отправляется вовсе.
Токены по краям строки снимаются, внутри заменяются на [[N]] и возвращаются после ответа.
То же для выражений кода: вызовов, camelCase и snake_case, а также для слэш-команд
и идентификаторов в кавычках.
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

# Слэш-команда: «/cp search». Модель переводит аргумент как текст: «/cp поиск». Слева только
# граница, поэтому «1/2», «and/or», «Message/Command» и пути не подходят. Аргументов не больше
# двух, служебное слово заканчивает команду: «/particle command to be used», «/cp reboot to reboot».
_COMMAND_STOP_WORDS = (
    "a|an|and|are|as|at|be|but|by|can|command|commands|could|do|does|for|from|has|have|if|in|is|it|may|"
    "must|not|of|on|or|should|that|the|then|this|to|was|when|which|while|will|with|would|you|your"
)
_COMMAND_END = r"(?![\w/\-]|\.\w)"
_COMMAND_ARG = (
    r"(?!(?:" + _COMMAND_STOP_WORDS + r")(?![\w\-]))"
    r"(?:@[a-z]|[a-z](?:[a-z0-9_:.\-]*[a-z0-9])?)" + _COMMAND_END
)
_SLASH_COMMAND = (
    r"""(?:^|(?<=[\s"'`«“(\[])|(?<=[§&][0-9a-fk-orA-FK-OR])|(?<=\\n))"""
    r"/[a-z][a-z0-9_\-]*" + _COMMAND_END + r"(?: +" + _COMMAND_ARG + r"){0,2}"
)
# Идентификатор в кавычках, в нижнем регистре, с точкой, подчёркиванием или двоеточием:
# "player.icon". Кавычки в токен не входят: модель вправе заменить их на «ёлочки».
_QUOTED_ID = (
    r"""(?<!\w')(?<=["'`«“„])[a-z][a-z0-9_\-]*(?:[._:][a-z0-9_\-]+)+(?=\\?["'`»”])"""
)
_PROTECTED_CODE_RE = re.compile(_SLASH_COMMAND + "|" + _QUOTED_ID)

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
    + "|" + _SLASH_COMMAND + "|" + _QUOTED_ID +
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


def protected_code_tokens(text: str) -> List[str]:
    """Слэш-команды и идентификаторы в кавычках из строки, по порядку."""
    return [match.group(0) for match in _PROTECTED_CODE_RE.finditer(text)]


def protected_code_changed(source: str, answer: str) -> bool:
    """Команды и идентификаторы из источника вернулись не все или не в том виде: это порча кода."""
    return sorted(protected_code_tokens(source)) != sorted(protected_code_tokens(answer))


# Тег: [слово], [/слово], <слово>, </слово>, можно со значением: [color=red], <font color=red>.
# Подпись из нескольких слов, [[N]], [1.5] и %s сюда не подходят.
_TAG_RE = re.compile(
    r"(?<!\[)\[/?[^\W\d_][\w\-]*(?:=[^\]\n]*)?\](?!\])"
    r"|</?[^\W\d_][\w\-:.]*(?:\s+[\w\-:.]+=[^<>\n]*)?\s*/?>"
)
_SQUARE_RE = re.compile(r"(?<!\[)\[[^\[\]\n]+\](?!\])")


def invented_tags(source: str, answer: str) -> List[str]:
    """
    Теги из ответа, которых нет в источнике (регистр не важен): «ease вы[/ru]», «тик[е]».
    Подпись в квадратных скобках модель вправе перевести: [disabled] → [выключено]. Такой тег
    не считается, если вместо него из источника пропала другая подпись и он на языке ответа.
    Закрывающий тег и угловые скобки не переводятся: <name> → <имя> это порча.
    """
    known = {tag.lower() for tag in _TAG_RE.findall(source)}
    new = [tag for tag in _TAG_RE.findall(answer) if tag.lower() not in known]
    if not new:
        return []
    returned = [tag.lower() for tag in _SQUARE_RE.findall(answer)]
    translated = 0
    for tag in _SQUARE_RE.findall(source):
        if tag.lower() in returned:
            returned.remove(tag.lower())
        else:
            translated += 1
    cyrillic_answer = bool(_CYRILLIC_RE.search(answer))
    found = []
    for tag in new:
        may_be_translation = tag[0] == "[" and tag[1] != "/" and (_CYRILLIC_RE.search(tag) or not cyrillic_answer)
        if may_be_translation and translated:
            translated -= 1
        else:
            found.append(tag)
    return found


# Языки на кириллице: английский ответ заметен по алфавиту.
_CYRILLIC_TARGETS = ("ru_", "uk_", "be_", "bg_", "kk_", "mk_", "mn_", "ba_", "tt_", "sr_sp")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+")
_TEXT_WORD_RE = re.compile(r"[^\W\d_]{2,}")
_KEY_COMBO_RE = re.compile(r"[^\W_]+(?:\s*\+\s*[^\W_]+)+")
_UNIT_WORDS = {"mb", "kb", "gb", "fe", "rf", "km", "cm", "mm", "kg", "ms", "fps", "tps", "hz"}
NO_CYRILLIC_MIN_WORDS = 2
# Столько слов и меньше: один и тот же текст в ответе считается названием, а не отказом переводить.
NAME_MAX_WORDS = 4


def is_cyrillic_lang(lang: str) -> bool:
    return lang.lower().startswith(_CYRILLIC_TARGETS)


def _plain_text(text: str) -> str:
    """Текст без ссылок, разметки, кода и защищённых токенов."""
    return MARKUP_RE.sub(" ", _URL_RE.sub(" ", text)).strip()


def text_word_count(text: str) -> int:
    """Сколько слов в тексте, без ссылок, разметки и кода."""
    return len(_WORD_RE.findall(_plain_text(text)))


def answer_without_cyrillic(source: str, answer: str, target_lang: str) -> bool:
    """
    В источнике от двух слов, а в ответе на язык с кириллицей нет ни одной её буквы:
    «Wraps itself in thin strings of silk» → «wraps itself in thin strings». Ответ, равный
    источнику, сюда не относится. Не брак: сочетание клавиш, единицы измерения и название,
    у которого все слова совпали с источником.
    """
    if answer == source or not is_cyrillic_lang(target_lang) or _CYRILLIC_RE.search(answer):
        return False
    plain_source = _plain_text(source)
    source_words = _TEXT_WORD_RE.findall(plain_source)
    if len(source_words) < NO_CYRILLIC_MIN_WORDS or _KEY_COMBO_RE.fullmatch(plain_source):
        return False
    if _TEXT_WORD_RE.findall(_plain_text(answer)) == source_words and len(source_words) <= NAME_MAX_WORDS:
        return False
    # Без снятия разметки: «mB» похоже на код camelCase.
    answer_words = _TEXT_WORD_RE.findall(_URL_RE.sub(" ", answer))
    return not (answer_words and all(word.lower() in _UNIT_WORDS for word in answer_words))


# Недоперевод: английское слово из источника внутри русского текста, «Т target licked», «ensnares цели».
# Слова в этом списке законны в русском тексте: логика, названия функций плавности и жаргон игры.
LEFTOVER_MIN_LETTERS = 4
LEFTOVER_ALLOWED = {
    "true", "false", "null", "ease", "expo", "circ", "quint", "quart", "quad", "sine", "cubic", "elastic", "bounce",
    "back", "vanilla",
}
_QUOTED_RE = re.compile(r"\"[^\"]*\"|«[^»]*»|“[^”]*”|`[^`]*`|(?<!\w)'[^']*'(?!\w)")
# Значения параметра: «Типы: both, world, server», «<normal|lazy|worried>».
_VALUE_LIST_RE = re.compile(r"\b[a-z]+(?:\s*[,|]\s*[a-z]+){2,}\b")
_LEFTOVER_WORD_RE = re.compile(
    r"(?<![\w'’./:=|<\-])[a-z]{%d,}(?![\w'’/=|>\-(]|[.:]\w)" % LEFTOVER_MIN_LETTERS
)
# Имя настройки с номером: «§ahour.<n>=<list>». После снятия разметки от него осталось бы обычное слово.
_NUMBERED_SETTING_RE = re.compile(r"[a-z_]+\.<[^<>\n]*>")
_SOURCE_WORD_RE = re.compile("[A-Za-z]+")


def leftover_english_words(source: str, answer: str, target_lang: str) -> List[str]:
    """
    Строчные английские слова от 4 букв из источника, которые стоят в русском ответе как обычные
    слова. Не считаются код, ссылки, кавычки, имена с большой буквы и списки значений.
    """
    if not is_cyrillic_lang(target_lang) or not _CYRILLIC_RE.search(answer):
        return []
    known = {word.lower() for word in _SOURCE_WORD_RE.findall(MARKUP_RE.sub(" ", source))}
    text = _VALUE_LIST_RE.sub(" ", _QUOTED_RE.sub(" ", _plain_text(_NUMBERED_SETTING_RE.sub(" ", answer))))
    return [w for w in _LEFTOVER_WORD_RE.findall(text) if w in known and w not in LEFTOVER_ALLOWED]


# Вступление модели перед переводом и слово в источнике, при котором такое начало законно.
# Голые «Хорошо», «Вот», «Пожалуйста» сюда не входят: это обычные переводы.
_INTRO_PHRASES = (
    (re.compile(r"хорошо\s*[,!:]\s*вот\b"), None),
    (re.compile(r"вот\s+(?:ваш\s+|сам\s+)?перевод\b"), None),
    (re.compile(r"i['\u2019]?m\s+ready\s+to\s+translate\b"), None),
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


# Точка сразу после переноса: «кожи игрока\\n. Примечание». Модель ставит её туда вместо конца строки.
# Перед строчной латиницей, цифрой и другой точкой это может быть текст: \\n.minecraft, \\n.5, \\n...
_NEWLINE_DOT_RE = re.compile(r"(?:\\n|\n)\.")
_STRAY_NEWLINE_DOT_RE = re.compile(r"(\\n|\n)\.(?![a-z0-9.])")


def strip_newline_dots(source: str, answer: Optional[str]) -> Optional[str]:
    """Убирает из ответа только такие точки и только если в источнике нет точки сразу после переноса."""
    if not answer or _NEWLINE_DOT_RE.search(source):
        return answer
    return _STRAY_NEWLINE_DOT_RE.sub(r"\1", answer)


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
