# src/processors/jar_lang.py
from __future__ import annotations
import io
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from .. import config
from ..utils.helpers import parse_lang_bytes
from ..utils.markup import text_word_count
from .lang_json import has_official_translation, keys_to_translate, merge_lang, source_text

_LANG_MEMBER = re.compile(r"(assets/[^/]+/lang/)([A-Za-z_]+)\.json$")
# Ванильный ru_ru игра берёт из своих ресурсов: перевод из чужого пака перекрыл бы его.
_VANILLA_LANG = "assets/minecraft/lang/"
_NESTED_JARS_DIR = "META-INF/jars/"
_MAX_NESTING = 4

# Сначала то, что видно сразу: названия предметов, блоков, меню и подсказки загрузки.
# Описания, всплывающие тексты и книги потом.
_LATER_KEY_RE = re.compile(r"(?:^|\.)(?:desc|description|lore|flavor|flavour|tooltip\w*|info|text|page\w*|book\w*)(?:\.|$)", re.I)
PRIORITY_MAX_WORDS = 14


def is_priority_key(key: str, text: str) -> bool:
    """Короткая строка интерфейса или подсказка загрузки (tip.*). Описания и книги не входят."""
    if key.startswith("tip.") or ".tip." in key:
        return True
    return text_word_count(text) <= PRIORITY_MAX_WORDS and not _LATER_KEY_RE.search(key)


def _translate_strings(sources: dict, keys: List[str], translator, target_lang: str, cancel_event) -> Dict[str, str]:
    translated = {}
    # Первый проход по сборке: только строки интерфейса, остальное ждёт своей очереди.
    key_filter = getattr(translator, "key_filter", None)
    if key_filter is not None:
        keys = [k for k in keys if key_filter(k, sources[k])]
    # Профиль «fast_cpu»: длинные тексты уходят сильной модели в отдельном потоке, пока основная
    # переводит остальное. Имеет смысл, только когда модели не делят одно устройство.
    background = {}
    if getattr(translator, "overlap_strong", False):
        long_keys = [k for k in keys if translator._client_for_long_text(sources[k]) is not None]
        if long_keys:
            pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="strong")
            background = {k: pool.submit(translator.translate, sources[k], target_lang=target_lang) for k in long_keys}
            pool.shutdown(wait=False)
    for k in keys:
        if k in background:
            continue
        if cancel_event is not None and cancel_event.is_set():
            break
        try:
            translated[k] = translator.translate(sources[k], target_lang=target_lang)
        except Exception:
            # Сбой на строке: ключ останется без перевода, игра покажет свой текст.
            translated[k] = sources[k]
    for k, future in background.items():
        try:
            translated[k] = future.result()
        except Exception:
            translated[k] = sources[k]
    # Порядок ключей как в файле: от него зависит порядок в паке.
    return {k: translated[k] for k in keys if k in translated}
    return translated


def _nested_jar_names(zf: zipfile.ZipFile) -> List[str]:
    """Вложенные jar мода Fabric: объявленные в fabric.mod.json и лежащие в META-INF/jars."""
    names = zf.namelist()
    if "fabric.mod.json" not in names:
        return []
    found = [n for n in names if n.startswith(_NESTED_JARS_DIR) and n.endswith(".jar")]
    meta = parse_lang_bytes(zf.read("fabric.mod.json")) or {}
    for entry in meta.get("jars") or []:
        declared = entry.get("file") if isinstance(entry, dict) else None
        if declared in names and declared not in found:
            found.append(declared)
    return sorted(found)


def _open_nested(zf: zipfile.ZipFile, name: str) -> Optional[zipfile.ZipFile]:
    try:
        return zipfile.ZipFile(io.BytesIO(zf.read(name)))
    except (zipfile.BadZipFile, KeyError):
        return None


def jar_has_lang(zf: zipfile.ZipFile, include_nested: bool, depth: int = 0) -> bool:
    for name in zf.namelist():
        n = name.replace("\\", "/")
        # Внутри jar пути идут от корня архива: "assets/<ns>/lang/en_us.json"
        if n.endswith("/lang/en_us.json") and (n.startswith("assets/") or "/assets/" in n):
            return True
    if include_nested and depth < _MAX_NESTING:
        for name in _nested_jar_names(zf):
            inner = _open_nested(zf, name)
            if inner is not None and jar_has_lang(inner, True, depth + 1):
                return True
    return False


def _process_archive(
    zf: zipfile.ZipFile, jar_label: str, pack, translator, log, write, target_lang, cancel_event,
    skip_vanilla: bool = False,
) -> int:
    processed = 0
    # Имя локали без учёта регистра: в старых модах встречается ru_RU.json
    lang_files = {}
    for name in zf.namelist():
        m = _LANG_MEMBER.fullmatch(name.replace("\\", "/"))
        if m:
            lang_files[(m.group(1), m.group(2).lower())] = name

    for (folder, locale), member in sorted(lang_files.items()):
        if locale != "en_us" or (skip_vanilla and folder == _VANILLA_LANG):
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

        keys = keys_to_translate(en, official, target_lang)
        rel_dst = f"{folder}{target_lang}.json"
        # До любого выхода: другой jar с тем же пространством имён не должен их перекрыть.
        if write:
            pack.add_lang(rel_dst, {}, [k for k in en if has_official_translation(k, en, official, target_lang)])
        if not keys:
            log(f"[SKIP][official] {jar_label}:{member}: всё переведено официально")
            continue

        sources = {k: source_text(k, en, official, target_lang) for k in keys}
        translated = _translate_strings(sources, keys, translator, target_lang, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            log(f"[STOP][discard] {jar_label}:{member}: прерван, в пак не добавлен")
            return processed
        fresh = sum(translated[k] != sources[k] for k in keys)
        processed += 1
        if not fresh:
            log(f"[SKIP][nothing-new] {jar_label}:{member}")
            continue

        if write:
            merged = merge_lang(en, official, translated, target_lang)
            pack.add_lang(rel_dst, merged, [k for k in merged if k in official and k not in translated])
            log(f"[OK][jar] {jar_label}:{member}: новых {fresh}, официальных {len(en) - len(keys)}")
        else:
            log(f"[dry][jar] {jar_label}:{member}: новых {fresh}, официальных {len(en) - len(keys)}")
    return processed


def _process_with_nested(zf, jar_label, pack, translator, log, write, target_lang, cancel_event, include_nested, depth=0) -> int:
    processed = _process_archive(zf, jar_label, pack, translator, log, write, target_lang, cancel_event)
    if not include_nested or depth >= _MAX_NESTING:
        return processed
    for name in _nested_jar_names(zf):
        if cancel_event is not None and cancel_event.is_set():
            break
        inner = _open_nested(zf, name)
        label = f"{jar_label}!{os.path.basename(name)}"
        if inner is None:
            log(f"[LWRN][jar] {label}: BadZipFile, skip")
            continue
        processed += _process_with_nested(
            inner, label, pack, translator, log, write, target_lang, cancel_event, True, depth + 1,
        )
    return processed


def process_jar_lang(
    jar_path: str,
    pack,
    translator,
    log: Callable[[str], None] = print,
    write: bool = True,
    target_lang: Optional[str] = None,
    cancel_event=None,
    include_nested: bool = False,
) -> int:
    """
    Ищет внутри JAR файлы assets/**/lang/en_us.json и добавляет в ресурспак перевод
    только недостающих ключей. Официальный перевод из JAR переносится без изменений.
    include_nested — ещё и вложенные jar мода Fabric (META-INF/jars).
    Так же читается zip с ресурсами, кроме ключей самой игры.
    Возвращает число файлов, где понадобился перевод. После «Стоп» недоведённый файл
    в пак не попадает: готовые строки остаются в кэше.
    """
    target_lang = target_lang or config.TARGET_LANG
    jar_label = os.path.basename(jar_path)
    try:
        with zipfile.ZipFile(os.path.abspath(jar_path), "r") as zf:
            if jar_path.lower().endswith(".zip"):
                return _process_archive(
                    zf, jar_label, pack, translator, log, write, target_lang, cancel_event, skip_vanilla=True,
                )
            return _process_with_nested(
                zf, jar_label, pack, translator, log, write, target_lang, cancel_event, include_nested,
            )
    except zipfile.BadZipFile:
        log(f"[LWRN][jar] {jar_label}: BadZipFile, skip")
        return 0
