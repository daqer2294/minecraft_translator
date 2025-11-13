# src/mirrorer.py
from __future__ import annotations
import os
import json
from typing import Callable, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from zipfile import ZipFile, BadZipFile

from .processors import lang_json, generic_json, ftb_snbt, kubejs_js, jar_lang
from .utils.helpers import ensure_dir_for_file
from . import config


def _rel(base: str, p: str) -> str:
    return os.path.relpath(p, start=base).replace("\\", "/")


def _is_lang_en_us(path_norm: str) -> bool:
    return path_norm.endswith("/lang/en_us.json") and "/assets/" in path_norm


def _is_patchouli_json(path_norm: str) -> bool:
    return "/assets/" in path_norm and "/patchouli_books/" in path_norm and "/en_us/" in path_norm and path_norm.endswith(".json")


def _is_tips_json(path_norm: str) -> bool:
    return "/assets/" in path_norm and "/tips/" in path_norm and path_norm.endswith(".json")


def _want_ftb_snbt(path_norm: str) -> bool:
    return "/ftbquests/" in path_norm and path_norm.endswith(".snbt")


def _want_kubejs_js(path_norm: str) -> bool:
    return "/kubejs/" in path_norm and path_norm.endswith(".js")


def _is_candidate(path_norm: str) -> bool:
    return (
        _is_lang_en_us(path_norm)
        or _is_patchouli_json(path_norm)
        or _is_tips_json(path_norm)
        or _want_ftb_snbt(path_norm)
        or _want_kubejs_js(path_norm)
    )


def _dst_exists(out_root: str, rel: str) -> bool:
    if rel.endswith("/lang/en_us.json"):
        dst_ru = os.path.join(out_root, os.path.dirname(rel), f"{config.TARGET_LANG}.json")
        return os.path.exists(dst_ru)
    if "/patchouli_books/" in rel and "/en_us/" in rel:
        rel_ru = rel.replace("/en_us/", f"/{config.TARGET_LANG}/")
        return os.path.exists(os.path.join(out_root, rel_ru))
    if "/tips/" in rel and rel.endswith(".json"):
        return os.path.exists(os.path.join(out_root, rel))
    if rel.endswith(".snbt"):
        return os.path.exists(os.path.join(out_root, rel))
    if rel.endswith(".js"):
        return os.path.exists(os.path.join(out_root, rel))
    return False


def _process_file(base_input: str, out_root: str, src_path: str, translator, write: bool, log: Callable[[str], None]) -> Tuple[bool, bool, bool]:
    """
    Возвращает (matched, ok, skipped)
      matched  — файл относится к поддерживаемым типам
      ok       — обработан без ошибок (включая SKIP как True)
      skipped  — был пропущен из-за уже существующего dst
    """
    rel = _rel(base_input, src_path)
    path_norm = src_path.replace("\\", "/")

    if not _is_candidate(path_norm):
        return False, False, False

    # уже готов — считаем как matched+ok+skipped
    if write and _dst_exists(out_root, rel):
        log(f"[SKIP][exists] {rel}")
        return True, True, True

    # 1) lang/en_us.json → ru_ru.json
    if _is_lang_en_us(path_norm):
        dst_ru = os.path.join(out_root, os.path.dirname(rel), f"{config.TARGET_LANG}.json")
        try:
            if write:
                os.makedirs(os.path.dirname(dst_ru), exist_ok=True)
                lang_json.translate_lang_json(src_path, dst_ru, translator)
                log(f"[OK][lang] {rel} → {os.path.relpath(dst_ru, out_root)}")
            else:
                with open(src_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                vals = [v for v in data.values() if isinstance(v, str)]
                if vals:
                    translator.translate_many(vals, target_lang=config.TARGET_LANG)
                log(f"[DRY][lang] {rel}")
            return True, True, False
        except Exception as e:
            log(f"[ERR][lang] {rel}: {e}")
            return True, False, False

    # 2) patchouli en_us → ru_ru
    if _is_patchouli_json(path_norm):
        rel_ru = rel.replace("/en_us/", f"/{config.TARGET_LANG}/")
        dst_ru = os.path.join(out_root, rel_ru)
        try:
            if write:
                os.makedirs(os.path.dirname(dst_ru), exist_ok=True)
                generic_json.translate_generic_json_file(src_path, dst_ru, translator)
                log(f"[OK][patchouli] {rel} → {rel_ru}")
            else:
                with open(src_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                acc = []
                def collect(n):
                    if isinstance(n, str): acc.append(n); return
                    if isinstance(n, list):
                        for i in n: collect(i)
                    elif isinstance(n, dict):
                        for v in n.values(): collect(v)
                collect(data)
                if acc:
                    translator.translate_many(acc, target_lang=config.TARGET_LANG)
                log(f"[DRY][patchouli] {rel}")
            return True, True, False
        except Exception as e:
            log(f"[ERR][patchouli] {rel}: {e}")
            return True, False, False

    # 3) tips/*.json → тот же путь
    if _is_tips_json(path_norm):
        dst = os.path.join(out_root, rel)
        try:
            if write:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                generic_json.translate_generic_json_file(src_path, dst, translator)
                log(f"[OK][tips] {rel}")
            else:
                with open(src_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                acc = []
                def collect(n):
                    if isinstance(n, str): acc.append(n); return
                    if isinstance(n, list):
                        for i in n: collect(i)
                    elif isinstance(n, dict):
                        for v in n.values(): collect(v)
                collect(data)
                if acc:
                    translator.translate_many(acc, target_lang=config.TARGET_LANG)
                log(f"[DRY][tips] {rel}")
            return True, True, False
        except Exception as e:
            log(f"[ERR][tips] {rel}: {e}")
            return True, False, False

    # 4) FTB Quests .snbt
    if _want_ftb_snbt(path_norm):
        dst = os.path.join(out_root, rel)
        try:
            if write:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(src_path, "r", encoding="utf-8") as f:
                    text = f.read()
                out = ftb_snbt.translate_ftb_snbt_text(text, translator)
                with open(dst, "w", encoding="utf-8", newline="\n") as f:
                    f.write(out)
                log(f"[OK][snbt] {rel}")
            else:
                log(f"[DRY][snbt] {rel}")
            return True, True, False
        except Exception as e:
            log(f"[ERR][snbt] {rel}: {e}")
            return True, False, False

    # 5) KubeJS .js
    if _want_kubejs_js(path_norm):
        dst = os.path.join(out_root, rel)
        try:
            if write:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                kubejs_js.translate_kubejs_script_file(src_path, dst, translator)
                log(f"[OK][kubejs] {rel}")
            else:
                log(f"[DRY][kubejs] {rel}")
            return True, True, False
        except Exception as e:
            log(f"[ERR][kubejs] {rel}: {e}")
            return True, False, False

    return False, False, False


def _jar_has_lang_en_us(jar_path: str) -> bool:
    if not config.SCAN_JAR_LANG:
        return True
    try:
        with ZipFile(jar_path) as zf:
            for name in zf.namelist():
                if name.endswith("assets/minecraft/lang/en_us.json"):
                    return True
                if "/assets/" in name and name.endswith("/lang/en_us.json"):
                    return True
    except BadZipFile:
        return False
    return False


def mirror_translate_dir(
    base_input: str,
    out_root: str,
    translator,
    log: Callable[[str], None] = print,
    write: bool = True,
    on_total: Optional[Callable[[int], None]] = None,
    on_tick: Optional[Callable[[int, int, int, int], None]] = None,  # (done, ok, err, skip) инкременты
) -> None:
    """
    Параллельный проход по кандидатам с визуализацией прогресса.
    """
    base_input = os.path.abspath(os.path.expanduser(base_input))
    out_root   = os.path.abspath(out_root)

    # 1) Скан: собираем кандидатов (не все файлы подряд)
    candidates: List[str] = []
    jar_files: List[str] = []

    for root, _, fnames in os.walk(base_input):
        r_norm = root.replace("\\", "/")
        for fname in fnames:
            full = os.path.join(root, fname)
            pnorm = full.replace("\\", "/")
            if fname.endswith(".jar") and "/mods/" in r_norm:
                jar_files.append(full)
                continue
            if _is_candidate(pnorm):
                candidates.append(full)

    # 2) Предскан JAR'ов
    jar_ready: List[str] = []
    for jp in jar_files:
        if _jar_has_lang_en_us(jp):
            jar_ready.append(jp)

    total = len(candidates) + len(jar_ready)
    if on_total:
        on_total(total)

    matched = 0
    ok = 0
    skipped = 0
    err = 0

    # 3) Параллельно обрабатываем кандидатов
    def tick(matched_inc: int, ok_inc: int, err_inc: int, skip_inc: int):
        nonlocal matched, ok, skipped, err
        matched += matched_inc
        ok += ok_inc
        skipped += skip_inc
        err += err_inc
        if on_tick:
            on_tick(matched_inc, ok_inc, err_inc, skip_inc)

    with ThreadPoolExecutor(max_workers=getattr(config, "MAX_WORKERS_FILES", 6)) as ex:
        futs = [ex.submit(_process_file, base_input, out_root, p, translator, write, log) for p in candidates]
        for f in as_completed(futs):
            try:
                m, good, skip = f.result()
            except Exception as e:
                log(f"[ERR] worker: {e}")
                tick(1, 0, 1, 0)
                continue
            if m:
                if good:
                    tick(1, 1, 0, 1 if skip else 0)
                else:
                    tick(1, 0, 1, 0)
            else:
                # теоретически не должно случаться, т.к. мы уже фильтровали
                tick(1, 0, 0, 0)

    # 4) Последовательно JAR-моды (внутри — собственная логика)
    for jp in jar_ready:
        try:
            jar_lang.process_jar(jp, base_input, out_root, translator, write=write, log=log)
            tick(1, 1, 0, 0)
        except Exception as e:
            log(f"[ERR][jar] {os.path.basename(jp)}: {e}")
            tick(1, 0, 1, 0)

    status = "DONE (dry-run)" if not write else "DONE"
    log(f"[{status}] total: {total}, matched: {matched}, ok: {ok}, skip: {skipped}, err: {err}, jars: {len(jar_ready)} / scanned: {len(jar_files)}")
