# src/processors/jar_lang.py
from __future__ import annotations
import os
import re
import zipfile
from typing import Callable, Dict, List, Optional

from .. import config
from ..utils.helpers import parse_lang_bytes
from .lang_json import keys_to_translate, merge_lang

_LANG_MEMBER = re.compile(r"(assets/[^/]+/lang/)([A-Za-z_]+)\.json$")


def _translate_strings(en: dict, keys: List[str], translator, target_lang: str, cancel_event) -> Dict[str, str]:
    translated = {}
    for k in keys:
        if cancel_event is not None and cancel_event.is_set():
            break
        try:
            translated[k] = translator.translate(en[k], target_lang=target_lang)
        except Exception:
            # Сбой на строке: ключ останется без перевода, игра покажет en_us.
            translated[k] = en[k]
    return translated


def process_jar_lang(
    jar_path: str,
    pack,
    translator,
    log: Callable[[str], None] = print,
    write: bool = True,
    target_lang: Optional[str] = None,
    cancel_event=None,
) -> int:
    """
    Ищет внутри JAR файлы assets/**/lang/en_us.json и добавляет в ресурспак перевод
    только недостающих ключей. Официальный перевод из JAR переносится без изменений.
    Возвращает число файлов, где понадобился перевод. После «Стоп» недоведённый файл
    в пак не попадает: готовые строки остаются в кэше.
    """
    target_lang = target_lang or config.TARGET_LANG
    jar_path = os.path.abspath(jar_path)
    jar_label = os.path.basename(jar_path)

    processed = 0

    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            # Имя локали без учёта регистра: в старых модах встречается ru_RU.json
            lang_files = {}
            for name in zf.namelist():
                m = _LANG_MEMBER.fullmatch(name.replace("\\", "/"))
                if m:
                    lang_files[(m.group(1), m.group(2).lower())] = name

            for (folder, locale), member in sorted(lang_files.items()):
                if locale != "en_us":
                    continue

                en = parse_lang_bytes(zf.read(member))
                if en is None:
                    log(f"[LWRN][jar] {jar_label}:{member}: bad json, skip")
                    continue

                official = {}
                official_member = lang_files.get((folder, target_lang.lower()))
                if official_member:
                    official = parse_lang_bytes(zf.read(official_member))
                    if official is None:
                        # Перезаписать нечитаемый официальный файл нельзя: в нём чужая работа.
                        log(f"[LWRN][jar] {jar_label}:{official_member}: официальный перевод не читается, skip")
                        continue

                keys = keys_to_translate(en, official)
                rel_dst = f"{folder}{target_lang}.json"
                if not keys:
                    log(f"[SKIP][official] {jar_label}:{member}: всё переведено официально")
                    continue

                translated = _translate_strings(en, keys, translator, target_lang, cancel_event)
                if cancel_event is not None and cancel_event.is_set():
                    log(f"[STOP][discard] {jar_label}:{member}: прерван, в пак не добавлен")
                    return processed
                fresh = sum(translated[k] != en[k] for k in keys)
                processed += 1
                if not fresh:
                    log(f"[SKIP][nothing-new] {jar_label}:{member}")
                    continue

                if write:
                    merged = merge_lang(en, official, translated)
                    pack.add_lang(rel_dst, merged, [k for k in merged if k in official and k not in translated])
                    log(f"[OK][jar] {jar_label}:{member}: новых {fresh}, официальных {len(en) - len(keys)}")
                else:
                    log(f"[dry][jar] {jar_label}:{member}: новых {fresh}, официальных {len(en) - len(keys)}")

    except zipfile.BadZipFile:
        log(f"[LWRN][jar] {jar_label}: BadZipFile, skip")

    return processed
