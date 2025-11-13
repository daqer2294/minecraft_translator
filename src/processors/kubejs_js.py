from __future__ import annotations
import re
from ..utils.helpers import ensure_dir_for_file
from .. import config

# функции/методы, в которых безопасно переводить 1-й строковый аргумент
_FUNCS = (
    r"Text\.of",
    r"tell",
    r"player\.tell",
    r"server\.tell",
    r"console\.log",
    r"sendMessage",
)

# "..." | '...'  (с экранированием)
_STR = r'(?P<q>["\'])(?P<txt>(?:\\.|(?!\1).)*?)\1'
# `...` без шаблонов ${...}
_TPL = r'`(?P<btxt>(?:\\.|(?!`).)*?)`'

# fn(<строка|бэктик>, ...)
_CALL_RE = re.compile(
    r'(?P<fn>(' + "|".join(_FUNCS) + r'))\s*\(\s*(?P<val>' + _STR + r'|' + _TPL + r')',
    flags=re.DOTALL,
)

def _unescape(s: str, quote: str) -> str:
    s = s.replace(r'\\', '\\')
    if quote == '"':
        s = s.replace(r'\"', '"')
    else:
        s = s.replace(r"\'", "'")
    return s

def _escape(s: str, quote: str) -> str:
    s = s.replace('\\', r'\\')
    if quote == '"':
        s = s.replace('"', r'\"')
    else:
        s = s.replace("'", r"\'")
    return s

def _translate_literal(m: re.Match, translator) -> str:
    if m.groupdict().get('q'):  # "..." | '...'
        q = m.group('q'); raw = m.group('txt')
        plain = _unescape(raw, q)
        out = translator.translate(plain, target_lang=config.TARGET_LANG)
        return f'{q}{_escape(out, q)}{q}'
    else:                       # `...` без ${}
        raw = m.group('btxt')
        if "${" in raw:
            return f'`{raw}`'
        out = translator.translate(raw, target_lang=config.TARGET_LANG)
        out = out.replace('\\', r'\\').replace('`', r'\`')
        return f'`{out}`'

def translate_kubejs_script_text(text: str, translator) -> str:
    def repl(m: re.Match) -> str:
        lit = m.group('val')
        sub = re.match(r'^' + _STR + r'$|^' + _TPL + r'$', lit, flags=re.DOTALL)
        if not sub:
            return m.group(0)
        new_lit = _translate_literal(sub, translator)
        # заменить только сам аргумент
        return m.group(0)[: m.start('val') - m.start()] + new_lit
    return _CALL_RE.sub(repl, text)

def translate_kubejs_script_file(src: str, dst: str, translator) -> None:
    with open(src, "r", encoding="utf-8") as f:
        data = f.read()
    out = translate_kubejs_script_text(data, translator)
    ensure_dir_for_file(dst)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)

# ✅ Алиас под вызов из mirrorer — именно его ищет код
def process_kubejs_script(src: str, dst: str, translator) -> None:
    translate_kubejs_script_file(src, dst, translator)
