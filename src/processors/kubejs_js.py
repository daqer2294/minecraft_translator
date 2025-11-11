# src/processors/kubejs_js.py
from __future__ import annotations
import os
import re
from ..utils.helpers import ensure_dir_for_file
from .. import config

# функции/методы, тексты которых можно без риска переводить
_FUNCS = (
    r"Text\.of",
    r"tell",
    r"player\.tell",
    r"server\.tell",
    r"console\.log",
    r"sendMessage",
)

# строковые литералы
_STR = r'(?P<q>["\'])(?P<txt>(?:\\.|(?!\1).)*?)\1'
# бэктики без шаблонов ${...} — только тогда берём
_TPL = r'`(?P<btxt>(?:\\.|(?!`).)*?)`'

# аргумент-строка как первый параметр
_CALL_RE = re.compile(
    r'(?P<fn>(' + "|".join(_FUNCS) + r'))\s*\(\s*(?P<val>' + _STR + r'|' + _TPL + r')',
    flags=re.DOTALL,
)

def _unescape(s: str, quote: str) -> str:
    s = s.replace(r'\\', '\\')
    if quote == '"':
        s = s.replace(r'\"', '"')
    elif quote == "'":
        s = s.replace(r"\'", "'")
    return s

def _escape(s: str, quote: str) -> str:
    s = s.replace('\\', r'\\')
    if quote == '"':
        s = s.replace('"', r'\"')
    elif quote == "'":
        s = s.replace("'", r"\'")
    return s

def _translate_literal(m, translator):
    if m.group('q'):  # "..." или '...'
        q = m.group('q'); raw = m.group('txt')
        plain = _unescape(raw, q)
        out = translator.translate(plain, target_lang=config.TARGET_LANG)
        return f'{q}{_escape(out, q)}{q}'
    else:             # `...` без ${}
        raw = m.group('btxt')
        if "${" in raw:
            return f'`{raw}`'  # пропускаем шаблоны
        out = translator.translate(raw, target_lang=config.TARGET_LANG)
        # в бэктиках экранируем только обратный слеш и `
        out = out.replace('\\', r'\\').replace('`', r'\`')
        return f'`{out}`'

def translate_kubejs_script_text(text: str, translator) -> str:
    def repl(m):
        # меняем только сам аргумент — имя функции и прочее оставляем
        start = m.start('val'); end = m.end('val')
        lit = m.group('val')
        # матчним вложенный литерал для удобства
        sub = re.match(r'^' + _STR + r'$|^' + _TPL + r'$', lit, flags=re.DOTALL)
        if not sub:
            return m.group(0)
        new_lit = _translate_literal(sub, translator)
        return m.group(0)[: start - m.start()] + new_lit
    return _CALL_RE.sub(repl, text)

def translate_kubejs_script_file(src: str, dst: str, translator) -> None:
    with open(src, "r", encoding="utf-8") as f:
        data = f.read()
    out = translate_kubejs_script_text(data, translator)
    ensure_dir_for_file(dst)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
