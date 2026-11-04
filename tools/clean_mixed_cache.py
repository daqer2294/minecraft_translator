"""
Удаляет из кэша переводы, которые отбраковывает проверка смеси алфавитов.

По умолчанию только показывает, что будет удалено. С --apply делает бэкап рядом
с кэшем и записывает новый файл. Существующий бэкап не перезаписывается.

    python -m tools.clean_mixed_cache
    python -m tools.clean_mixed_cache --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import Dict, List, Tuple

from src import config
from src.utils.markup import has_mixed_script_word

BACKUP_SUFFIX = ".before-mixed-cleanup"


def rejected_entries(data: Dict[str, Dict[str, str]]) -> List[Tuple[str, str, str]]:
    return [
        (lang, source, value)
        for lang, mapping in data.items()
        for source, value in mapping.items()
        if has_mixed_script_word(value)
    ]


def clean_cache(path: str, apply: bool = False) -> Dict[str, int]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not all(isinstance(m, dict) for m in data.values()):
        raise ValueError("Ожидается кэш вида {язык: {источник: перевод}}")

    rejected = rejected_entries(data)
    before = sum(len(m) for m in data.values())
    stats = {"before": before, "removed": len(rejected), "after": before - len(rejected)}
    if not apply or not rejected:
        return stats

    backup = path + BACKUP_SUFFIX
    if os.path.exists(backup):
        raise FileExistsError(f"Бэкап уже есть, не перезаписываю: {backup}")
    shutil.copy2(path, backup)

    for lang, source, _ in rejected:
        del data[lang][source]
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
    action = "удалено" if args.apply else "будет удалено"
    print(f"{args.cache}: было {stats['before']}, {action} {stats['removed']}, останется {stats['after']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
