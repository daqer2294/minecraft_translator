# src/mirrorer.py
from __future__ import annotations
import os
import json
from typing import Callable

from .processors import lang_json, generic_json, ftb_snbt, kubejs_js, jar_lang
from . import config


def _is_lang_en_us(path_norm: str) -> bool:
    return path_norm.endswith("/lang/en_us.json") and "/assets/" in path_norm

def _is_patchouli_json(path_norm: str) -> bool:
    return ("/assets/" in path_norm and "/patchouli_books/" in path_norm
            and "/en_us/" in path_norm and path_norm.endswith(".json"))

def _is_tips_json(path_norm: str) -> bool:
    return "/assets/" in path_norm and "/tips/" in path_norm and path_norm.endswith(".json")

def _is_ftb_quests_snbt(path_norm: str) -> bool:
    return ("/ftbquests/" in path_norm) and path_norm.endswith(".snbt")

def _is_kubejs_script(path_norm: str) -> bool:
    # kubejs/server_scripts|client_scripts/**/*.js
    return ("/kubejs/" in path_norm) and path_norm.endswith(".js") and (
        "/server_scripts/" in path_norm or "/client_scripts/" in path_norm
    )

def _relpath(from_root: str, full_path: str) -> str:
    return os.path.relpath(full_path, start=from_root).replace("\\", "/")

def _scan_jars_and_translate(base_input: str, out_root: str, translator, log) -> int:
    """Перебираем mods/*.jar и создаём overlay ru_ru.json в kubejs/assets/*/lang."""
    mods_dir = os.path.join(base_input, "mods")
    if not os.path.isdir(mods_dir):
        return 0
    ok = 0
    for name in os.listdir(mods_dir):
        if not name.endswith(".jar"):
            continue
        ok += jar_lang.translate_from_jar(os.path.join(mods_dir, name), out_root, translator, log=log)
    if ok:
        log(f"[jar] translated mods: {ok}")
    return ok

def mirror_translate_dir(
    base_input: str,
    out_root: str,
    translator,
    log: Callable[[str], None] = print,
    write: bool = True,
) -> tuple[int, int]:
    """
    Переводим:
      - assets/*/lang/en_us.json                  -> lang/ru_ru.json
      - assets/**/patchouli_books/**/en_us/*.json -> /ru_ru/
      - assets/**/tips/*.json                     -> на месте (в out_root)
      - config|defaultconfigs/ftbquests/*.snbt    -> на месте
      - (NEW) mods/*.jar                          -> overlay в overrides/kubejs/assets/<modid>/lang/ru_ru.json
      - (NEW) kubejs/*_scripts/**/*.js            -> перевод строк в известных функциях
    """
    base_input = os.path.abspath(os.path.expanduser(base_input))
    out_root = os.path.abspath(out_root)

    files_total, files_translated = 0, 0

    # 0) jar-моды — обрабатываем один раз «по каталогу mods»
    if write:
        _scan_jars_and_translate(base_input, out_root, translator, log)

    SKIP_DIRS = {
        "textures", "models", "sounds", "blockstates", "recipes", "loot_tables",
        "advancements", "shaders", "particles", "font", "icons", "data",
        ".git", ".idea", "__pycache__", "mods"  # mods отдельно обработали
    }
    IMPORTANT_HINTS = ("/lang", "/patchouli_books", "/tips", "/ftbquests/", "/kubejs/")

    for root, dirnames, files in os.walk(base_input, topdown=True):
        root_norm = root.replace("\\", "/")

        if ("/assets/" not in root_norm) and ("/ftbquests/" not in root_norm) and ("/kubejs/" not in root_norm):
            dirnames[:] = [
                d for d in dirnames
                if ("assets" in os.path.join(root_norm, d).replace("\\", "/"))
                or ("ftbquests" in d)
                or ("kubejs" in d)
            ]
            files[:] = []
            continue

        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        if not any(h in root_norm for h in IMPORTANT_HINTS):
            dirnames[:] = [
                d for d in dirnames
                if d in {"lang", "patchouli_books", "tips", "ftbquests", "kubejs"}
                or any(h in os.path.join(root_norm, d).replace("\\", "/") for h in IMPORTANT_HINTS)
            ]

        for fname in files:
            src_path = os.path.join(root, fname)
            rel = _relpath(base_input, src_path)
            path_norm = src_path.replace("\\", "/")

            # 1) lang -> ru_ru.json
            if _is_lang_en_us(path_norm):
                files_total += 1
                dst_ru = os.path.join(out_root, os.path.dirname(rel), f"{config.TARGET_LANG}.json")
                try:
                    if write:
                        os.makedirs(os.path.dirname(dst_ru), exist_ok=True)
                        lang_json.translate_lang_json(src_path, dst_ru, translator)
                    else:
                        with open(src_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        for _, v in data.items():
                            if isinstance(v, str):
                                translator.translate(v, target_lang=config.TARGET_LANG)
                        log(f"[dry][lang] {rel} → {os.path.relpath(dst_ru, out_root)}")
                    files_translated += 1
                except Exception as e:
                    log(f"[ERR] lang: {rel}: {e}")
                continue

            # 2) patchouli
            if _is_patchouli_json(path_norm):
                files_total += 1
                rel_ru = rel.replace("/en_us/", f"/{config.TARGET_LANG}/")
                dst_ru = os.path.join(out_root, rel_ru)
                try:
                    if write:
                        os.makedirs(os.path.dirname(dst_ru), exist_ok=True)
                        generic_json.translate_generic_json_file(src_path, dst_ru, translator)
                    else:
                        with open(src_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        def walk(v):
                            if isinstance(v, str): translator.translate(v, target_lang=config.TARGET_LANG)
                            elif isinstance(v, list):
                                for i in v: walk(i)
                            elif isinstance(v, dict):
                                for vv in v.values(): walk(vv)
                        walk(data)
                        log(f"[dry][patchouli] {rel} → {rel_ru}")
                    files_translated += 1
                except Exception as e:
                    log(f"[ERR] patchouli: {rel}: {e}")
                continue

            # 3) tips/*.json
            if _is_tips_json(path_norm):
                files_total += 1
                dst_same = os.path.join(out_root, rel)
                try:
                    if write:
                        os.makedirs(os.path.dirname(dst_same), exist_ok=True)
                        generic_json.translate_generic_json_file(src_path, dst_same, translator)
                    else:
                        with open(src_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        def walk(v):
                            if isinstance(v, str): translator.translate(v, target_lang=config.TARGET_LANG)
                            elif isinstance(v, list):
                                for i in v: walk(i)
                            elif isinstance(v, dict):
                                for vv in v.values(): walk(vv)
                        walk(data)
                        log(f"[dry][tips] {rel}")
                    files_translated += 1
                except Exception as e:
                    log(f"[ERR] tips: {rel}: {e}")
                continue

            # 4) FTB Quests .snbt
            if _is_ftb_quests_snbt(path_norm):
                files_total += 1
                dst_same = os.path.join(out_root, rel)
                try:
                    if write:
                        os.makedirs(os.path.dirname(dst_same), exist_ok=True)
                        ftb_snbt.translate_ftb_snbt_file(src_path, dst_same, translator)
                    else:
                        log(f"[dry][ftbquests] {rel}")
                    files_translated += 1
                except Exception as e:
                    log(f"[ERR] ftbquests: {rel}: {e}")
                continue

            # 5) KubeJS scripts
            if config.INCLUDE_KUBEJS_JS and _is_kubejs_script(path_norm):
                files_total += 1
                dst_same = os.path.join(out_root, rel)
                try:
                    if write:
                        os.makedirs(os.path.dirname(dst_same), exist_ok=True)
                        kubejs_js.translate_kubejs_script_file(src_path, dst_same, translator)
                    else:
                        log(f"[dry][kubejs] {rel}")
                    files_translated += 1
                except Exception as e:
                    log(f"[ERR] kubejs: {rel}: {e}")
                continue

            # остальное не трогаем
            continue

    status = "DONE (dry-run)" if not write else "DONE"
    log(f"[{status}] total matched files: {files_total}, translated: {files_translated}")
    return files_total, files_translated
