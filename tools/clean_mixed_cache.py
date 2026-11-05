"""
Удаляет из кэша переводы, которые не проходят текущие проверки ответа модели:
смесь алфавитов, иероглифы, текст наших промптов, выдуманный ответ на короткую строку,
вступление или нераскрытая маска, латинское слово не из источника,
изменённая команда или идентификатор, выдуманный тег, потеря или искажение разметки и кода.

По умолчанию только показывает, что будет удалено и почему. С --apply делает бэкап
рядом с кэшем и записывает новый файл. Существующий бэкап не перезаписывается.

--also-from добавляет к удалению строки из файла JSON (список исходных строк): брак,
который проверки не ловят. --retranslate после удаления сразу переводит эти строки
сильной моделью в Ollama, минуя основную. Ответы проходят все те же проверки, брак
в кэш не попадает. --keep-from оставляет строки из файла на месте: брак, по которому
ещё нет решения.

    python -m tools.clean_mixed_cache
    python -m tools.clean_mixed_cache --apply
    python -m tools.clean_mixed_cache --apply --also-from list.json --retranslate
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
from typing import Dict, Iterable, List, NamedTuple, Optional

from src import config
from src.translators import PROMPT_LEAK, Translator, _extract_tokens, _has_foreign_cjk, _is_bare_id
from src.utils.cache import TranslationCache
from src.utils.helpers import is_untranslatable
from src.utils.markup import (
    foreign_latin_words,
    has_intro_or_raw_mask,
    has_mixed_script_word,
    invented_tags,
    looks_hallucinated,
    protected_code_changed,
    protected_code_tokens,
)

BACKUP_SUFFIX = ".before-tag-cleanup"
LISTED_REASON = "указана вручную"


class Rejected(NamedTuple):
    lang: str
    source: str
    value: str
    reason: str


class Retranslated(NamedTuple):
    lang: str
    source: str
    value: str
    passed: bool


def rejection_reason(source: str, value: str, lang: str) -> Optional[str]:
    """Почему запись брак, или None. Перевод, равный оригиналу, браком не считается."""
    if value == source:
        return None
    # Такие строки Translator отдаёт как есть, не заглядывая в кэш: запись ни на что не влияет.
    if _is_bare_id(source) or is_untranslatable(source):
        return None
    if has_mixed_script_word(value):
        return "смесь алфавитов"
    if _has_foreign_cjk(value, lang):
        return "иероглифы"
    leak = PROMPT_LEAK.explain(value, source)
    if leak:
        return f"текст промпта: {leak}"
    if looks_hallucinated(source, value):
        return "выдуманный ответ на короткую строку"
    if has_intro_or_raw_mask(source, value):
        return "вступление или нераскрытая маска"
    words = foreign_latin_words(source, value)
    if words:
        return f"латинское слово не из источника: {words[:3]}"
    if protected_code_changed(source, value):
        return f"команда или идентификатор: было {protected_code_tokens(source)}, стало {protected_code_tokens(value)}"
    tags = invented_tags(source, value)
    if tags:
        return f"выдуманный тег: {tags[:3]}"
    source_tokens, value_tokens = _extract_tokens(source), _extract_tokens(value)
    if source_tokens != value_tokens:
        lost = sorted(set(source_tokens) - set(value_tokens))
        added = sorted(set(value_tokens) - set(source_tokens))
        return f"разметка или код: потеряно {lost[:3]}, появилось {added[:3]}"
    return None


def rejected_entries(data: Dict[str, Dict[str, str]]) -> List[Rejected]:
    found = []
    for lang, mapping in data.items():
        for source, value in mapping.items():
            reason = rejection_reason(source, value, lang)
            if reason:
                found.append(Rejected(lang, source, value, reason))
    return found


def clean_cache(
    path: str, apply: bool = False, also: Iterable[str] = (), also_lang: str = "ru_ru", keep: Iterable[str] = (),
) -> Dict[str, object]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not all(isinstance(m, dict) for m in data.values()):
        raise ValueError("Ожидается кэш вида {язык: {источник: перевод}}")

    rejected = rejected_entries(data)
    found = {(entry.lang, entry.source) for entry in rejected}
    mapping = data.get(also_lang, {})
    keep = list(keep)
    missing = [source for source in [*also, *keep] if source not in mapping]
    if missing:
        raise KeyError(f"Этих строк нет в кэше, ничего не удалено: {missing}")
    for source in dict.fromkeys(also):
        if (also_lang, source) not in found:
            rejected.append(Rejected(also_lang, source, mapping[source], LISTED_REASON))
    kept = [entry for entry in rejected if entry.lang == also_lang and entry.source in keep]
    rejected = [entry for entry in rejected if entry not in kept]
    before = sum(len(m) for m in data.values())
    stats: Dict[str, object] = {
        "before": before, "removed": len(rejected), "after": before - len(rejected), "entries": rejected,
        "kept": kept,
    }
    if not apply or not rejected:
        return stats

    backup = path + BACKUP_SUFFIX
    if os.path.exists(backup):
        raise FileExistsError(f"Бэкап уже есть, не перезаписываю: {backup}")
    shutil.copy2(path, backup)

    for entry in rejected:
        del data[entry.lang][entry.source]
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return stats


def strong_ollama_client(cfg=None):
    """Сильная модель именно из Ollama. Запасной вариант фабрики здесь не подходит: это та же Qwen."""
    from src.llm.factory import build_strong_client
    from src.llm.openai_compatible import OpenAICompatibleClient

    client, reason = build_strong_client(cfg or config.PROVIDER)
    if client is None:
        raise RuntimeError(reason)
    if not isinstance(client, OpenAICompatibleClient):
        raise RuntimeError(f"Сильной модели нет в Ollama, а {client.name} брать нельзя.")
    return client


def retranslate(path: str, entries: Iterable[Rejected], client) -> List[Retranslated]:
    """Переводит строки только этим клиентом. Брак по любой проверке в кэш не попадает."""
    translator = Translator(client, TranslationCache(path), strict=True, log=lambda line: None)
    results = []
    try:
        for entry in entries:
            with contextlib.redirect_stdout(io.StringIO()):
                value = translator.translate(entry.source, entry.lang, client=client)
            results.append(Retranslated(entry.lang, entry.source, value, value != entry.source))
    finally:
        translator.flush()
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default=config.DEFAULT_CACHE_PATH)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--also-from", help="файл JSON со списком строк, которые удалить вместе с браком")
    parser.add_argument("--also-lang", default="ru_ru", help="язык записей из --also-from")
    parser.add_argument("--keep-from", help="файл JSON со списком строк, которые оставить в кэше, даже если они брак")
    parser.add_argument("--retranslate", action="store_true", help="сразу перевести удалённое сильной моделью в Ollama")
    args = parser.parse_args(argv)
    if args.retranslate and not args.apply:
        parser.error("--retranslate работает только вместе с --apply")
    also: List[str] = []
    if args.also_from:
        with open(args.also_from, "r", encoding="utf-8") as f:
            also = json.load(f)
    keep: List[str] = []
    if args.keep_from:
        with open(args.keep_from, "r", encoding="utf-8") as f:
            keep = json.load(f)
    # Клиент проверяется до удаления: без модели записи не должны пропасть впустую.
    client = strong_ollama_client() if args.retranslate else None
    stats = clean_cache(args.cache, apply=args.apply, also=also, also_lang=args.also_lang, keep=keep)
    if not args.apply:
        for i, entry in enumerate(stats["entries"], 1):
            print(f"{i}. [{entry.reason}]\n   {entry.source!r}\n   -> {entry.value!r}")
    for entry in stats["kept"]:
        print(f"Оставлено без удаления: [{entry.reason}]\n   {entry.source!r}\n   -> {entry.value!r}")
    action = "удалено" if args.apply else "будет удалено"
    print(f"{args.cache}: было {stats['before']}, {action} {stats['removed']}, останется {stats['after']}")
    if client is not None:
        print(f"Перевод сильной моделью: {client.name}")
        results = retranslate(args.cache, stats["entries"], client)
        for i, item in enumerate(results, 1):
            mark = "" if item.passed else " [НЕ ПРОШЛО, ОСТАЛОСЬ НА АНГЛИЙСКОМ]"
            print(f"{i}. {item.source!r}\n   -> {item.value!r}{mark}")
        passed = sum(item.passed for item in results)
        print(f"Прошло все проверки: {passed} из {len(results)}, осталось на английском: {len(results) - passed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
