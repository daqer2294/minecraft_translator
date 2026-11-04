from __future__ import annotations
import re
from typing import List, Optional
from ..utils.helpers import ensure_dir_for_file

# Переводим только первый строковый аргумент вызовов, которые видит игрок.
# console.log читают разработчики, sendMessage бывает служебным, поэтому их не трогаем.
_CALL_RE = re.compile(
    r"\b(?:Text\.of|tell)\s*\(\s*(?:"
    r"(?P<q>[\"'])(?P<txt>(?:\\.|(?!(?P=q))[^\\\n])*)(?P=q)"
    r"|`(?P<btxt>(?:\\.|[^`\\])*)`"
    r")"
)
# Экранирование, которое мы умеем сохранить. Остальное (\u00a7 и т.п.) не трогаем.
_UNSAFE_ESCAPE = re.compile(r"\\(?![nt\"'`])")


def _code_positions(text: str) -> List[bool]:
    """True для символов вне строк и комментариев: вызов внутри строки не вызов."""
    code = [True] * len(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
        elif c in "\"'`":
            end = i + 1
            while end < n and text[end] != c:
                end += 2 if text[end] == "\\" else 1
            end = min(end + 1, n)
        else:
            i += 1
            continue
        for j in range(i, end):
            code[j] = False
        i = end
    return code


def _encode(text: str, quote: str) -> str:
    """Записывает перевод литералом: кавычки и косые черты экранируются, строка не рвётся."""
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            following = text[i + 1:i + 2]
            if following in ("n", "t", '"', "'", "`"):
                out.append(c + following)
                i += 2
                continue
            out.append("\\\\")
        elif c == quote:
            out.append("\\" + c)
        elif c == "\n" and quote != "`":
            out.append("\\n")
        elif c == "\r":
            pass
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _translate_literal(raw: str, quote: str, translator) -> Optional[str]:
    if "${" in raw or _UNSAFE_ESCAPE.search(raw):
        return None
    plain = raw.replace("\\" + quote, quote)
    translated = translator.translate(plain)
    return None if translated == plain else _encode(translated, quote)


def translate_kubejs_script_text(text: str, translator) -> str:
    code = _code_positions(text)

    def repl(m: re.Match) -> str:
        if not code[m.start()]:
            return m.group(0)
        quote = m.group("q") or "`"
        raw = m.group("txt") if m.group("q") else m.group("btxt")
        new = _translate_literal(raw, quote, translator)
        if new is None:
            return m.group(0)
        group = "txt" if m.group("q") else "btxt"
        return m.group(0)[: m.start(group) - m.start()] + new + quote

    return _CALL_RE.sub(repl, text)


def translate_kubejs_script_file(src: str, dst: str, translator) -> bool:
    """Пишет скрипт, только если текст изменился. Возвращает True, если записал."""
    with open(src, "r", encoding="utf-8-sig") as f:
        data = f.read()
    out = translate_kubejs_script_text(data, translator)
    if out == data:
        return False
    ensure_dir_for_file(dst)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
    return True
