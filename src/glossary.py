# src/glossary.py
"""
Словарь терминов и имён для перевода. Термины не придумываются: они берутся из
официальных языковых файлов сборки, где английскому имени соответствует русское.
"""
from __future__ import annotations

import glob
import json
import os
import re
import zipfile
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .processors.jar_lang import _LANG_MEMBER, _nested_jar_names, _open_nested
from .utils.helpers import parse_lang_bytes

_CYRILLIC_RE = re.compile("[Ѐ-ԯ]")
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Порядок важен: одно английское имя остаётся за первой категорией.
_CATEGORIES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("pokemon", re.compile(r"cobblemon\.species\.[^.]+\.name")),
    ("move", re.compile(r"cobblemon\.move\.[^.]+")),
    ("ability", re.compile(r"cobblemon\.ability\.[^.]+")),
    ("type", re.compile(r"cobblemon\.type\.[^.]+")),
    ("item", re.compile(r"item\.[^.]+\.[^.]+")),
    ("block", re.compile(r"block\.[^.]+\.[^.]+")),
    ("entity", re.compile(r"entity\.[^.]+\.[^.]+")),
    ("effect", re.compile(r"effect\.[^.]+\.[^.]+")),
)
CATEGORY_ORDER = [name for name, _ in _CATEGORIES]
_MAX_NESTING = 4


@dataclass(frozen=True)
class Term:
    en: str
    ru: str
    category: str


def _category_of(key: str) -> Optional[str]:
    for name, pattern in _CATEGORIES:
        if pattern.fullmatch(key):
            return name
    return None


def _is_usable(en: str, ru: object) -> bool:
    """Настоящий русский вариант: с кириллицей, без подстановок и не сокращение вроде «Огнен.»"""
    return (
        isinstance(ru, str)
        and bool(_CYRILLIC_RE.search(ru))
        and not ru.rstrip().endswith(".")
        and "%" not in en
        and "%" not in ru
        and len(en) >= 2
    )


class Collector:
    """Собирает пары en_us и официальный перевод. Считает имена без русского варианта."""

    def __init__(self) -> None:
        self.terms: Dict[str, Term] = {}
        self.without_ru: Dict[str, set] = {}
        self.conflicts = 0

    def add_files(self, en: dict, official: dict) -> None:
        for key, value in en.items():
            category = _category_of(key)
            if category is None or not isinstance(value, str) or not value.strip():
                continue
            ru = official.get(key)
            if not _is_usable(value, ru):
                self.without_ru.setdefault(category, set()).add(value)
                continue
            known = self.terms.get(value)
            if known is None:
                self.terms[value] = Term(value, ru, category)
            elif known.ru != ru:
                self.conflicts += 1
                # Имя покемона или приёма важнее предмета с тем же названием
                if CATEGORY_ORDER.index(category) < CATEGORY_ORDER.index(known.category):
                    self.terms[value] = Term(value, ru, category)

    def add_archive(self, zf: zipfile.ZipFile, target_lang: str, depth: int = 0) -> None:
        files = {}
        for name in zf.namelist():
            match = _LANG_MEMBER.fullmatch(name.replace("\\", "/"))
            if match:
                files[(match.group(1), match.group(2).lower())] = name
        for (folder, locale), member in files.items():
            official_member = files.get((folder, target_lang.lower()))
            if locale != "en_us" or official_member is None:
                continue
            en, official = parse_lang_bytes(zf.read(member)), parse_lang_bytes(zf.read(official_member))
            if en and official:
                self.add_files(en, official)
        if depth < _MAX_NESTING:
            for name in _nested_jar_names(zf):
                inner = _open_nested(zf, name)
                if inner is not None:
                    self.add_archive(inner, target_lang, depth + 1)

    def counts(self) -> Dict[str, int]:
        found: Dict[str, int] = {name: 0 for name in CATEGORY_ORDER}
        for term in self.terms.values():
            found[term.category] += 1
        return found

    def missing_counts(self) -> Dict[str, int]:
        """Имена без русского варианта. То, что перевод нашёлся в другом файле, не считается."""
        return {
            name: len({en for en in self.without_ru.get(name, ()) if en not in self.terms})
            for name in CATEGORY_ORDER
        }


def _vanilla_roots(base_input: str) -> List[str]:
    """Папки с ресурсами игры: Install у CurseForge рядом со сборкой и обычная .minecraft."""
    roots, path = [], os.path.abspath(base_input)
    for _ in range(4):
        path = os.path.dirname(path)
        roots.append(os.path.join(path, "Install"))
    roots.append(os.path.expanduser("~/Library/Application Support/minecraft"))
    roots.append(os.path.expanduser("~/.minecraft"))
    roots.append(os.path.join(os.environ.get("APPDATA", ""), ".minecraft"))
    return [root for root in roots if os.path.isdir(os.path.join(root, "assets", "indexes"))]


def read_vanilla_lang(base_input: str, game_version: str, target_lang: str) -> Tuple[dict, dict]:
    """en_us и перевод самой игры. Пустые словари, если ресурсы не нашлись: без них словарь всё равно работает."""
    if not game_version:
        return {}, {}
    for root in _vanilla_roots(base_input):
        jar = os.path.join(root, "versions", game_version, f"{game_version}.jar")
        if not os.path.isfile(jar):
            continue
        for index_path in sorted(glob.glob(os.path.join(root, "assets", "indexes", "*.json")), reverse=True):
            try:
                with open(index_path, encoding="utf-8") as f:
                    entry = json.load(f)["objects"].get(f"minecraft/lang/{target_lang.lower()}.json")
                if not entry:
                    continue
                digest = entry["hash"]
                with open(os.path.join(root, "assets", "objects", digest[:2], digest), "rb") as f:
                    official = parse_lang_bytes(f.read())
                with zipfile.ZipFile(jar) as zf:
                    en = parse_lang_bytes(zf.read("assets/minecraft/lang/en_us.json"))
                if en and official:
                    return en, official
            except (OSError, KeyError, ValueError, zipfile.BadZipFile):
                continue
    return {}, {}


def collect_official(
    archives: Iterable[str], target_lang: str, base_input: str = "", game_version: str = "",
) -> Collector:
    collector = Collector()
    # Сначала игра, затем моды: при совпадении имён правило категорий одно.
    vanilla_en, vanilla_ru = read_vanilla_lang(base_input, game_version, target_lang) if base_input else ({}, {})
    collector.add_files(vanilla_en, vanilla_ru)
    for path in archives:
        try:
            with zipfile.ZipFile(path) as zf:
                collector.add_archive(zf, target_lang)
        except (OSError, zipfile.BadZipFile):
            continue
    return collector


@dataclass(frozen=True)
class FixedTerm:
    """Общий термин из файла: формы в источнике и корни русского варианта."""
    en: str
    ru: str
    forms: Tuple[str, ...]
    roots: Tuple[str, ...]
    check: bool


def load_fixed_terms(target_lang: str) -> List[FixedTerm]:
    """Файл src/data/glossary_<язык>.json. Для языка без файла общих терминов нет."""
    path = os.path.join(_DATA_DIR, f"glossary_{target_lang.lower()}.json")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [
            FixedTerm(item["en"], item["ru"], tuple(item["forms"]), tuple(item["roots"]), bool(item["check"]))
            for item in json.load(f)["terms"]
        ]
