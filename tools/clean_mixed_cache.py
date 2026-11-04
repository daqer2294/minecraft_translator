"""
Удаляет из кэша переводы, которые не проходят текущие проверки ответа модели:
смесь алфавитов, иероглифы, текст наших промптов, потеря или искажение разметки и кода.

По умолчанию только показывает, что будет удалено и почему. С --apply делает бэкап
рядом с кэшем и записывает новый файл. Существующий бэкап не перезаписывается.

    python -m tools.clean_mixed_cache
    python -m tools.clean_mixed_cache --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import Dict, List, NamedTuple, Optional

from src import config
from src.translators import PROMPT_LEAK, _extract_tokens, _has_foreign_cjk, _is_bare_id
from src.utils.helpers import is_untranslatable
from src.utils.markup import has_mixed_script_word

BACKUP_SUFFIX = ".before-leak-cleanup"


class Rejected(NamedTuple):
    lang: str
    source: str
    value: str
    reason: str


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


def clean_cache(path: str, apply: bool = False) -> Dict[str, object]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not all(isinstance(m, dict) for m in data.values()):
        raise ValueError("Ожидается кэш вида {язык: {источник: перевод}}")

    rejected = rejected_entries(data)
    before = sum(len(m) for m in data.values())
    stats: Dict[str, object] = {
        "before": before, "removed": len(rejected), "after": before - len(rejected), "entries": rejected,
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default=config.DEFAULT_CACHE_PATH)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    stats = clean_cache(args.cache, apply=args.apply)
    if not args.apply:
        for i, entry in enumerate(stats["entries"], 1):
            print(f"{i}. [{entry.reason}]\n   {entry.source!r}\n   -> {entry.value!r}")
    action = "удалено" if args.apply else "будет удалено"
    print(f"{args.cache}: было {stats['before']}, {action} {stats['removed']}, останется {stats['after']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
