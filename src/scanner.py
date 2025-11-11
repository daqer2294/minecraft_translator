# src/scanner.py
from __future__ import annotations
import os
import json
import zipfile

from .processors import lang_json, generic_json
from .utils.helpers import ensure_dir_for_file
from . import config


def _scan_patchouli_in_assets(assets_dir: str, out_root: str, translator, processed: int) -> int:
    """assets/**/patchouli_books/**/en_us/**.json → .../ru_ru/..."""
    if not os.path.isdir(assets_dir):
        return processed
    for root, _, files in os.walk(assets_dir):
        root_norm = root.replace("\\", "/")
        if "/patchouli_books/" in root_norm and "/en_us/" in root_norm:
            for fname in files:
                if not fname.endswith(".json"):
                    continue
                src_path = os.path.join(root, fname)
                rel = os.path.relpath(src_path, start=assets_dir).replace("\\", "/")
                rel_ru = rel.replace("/en_us/", f"/{config.TARGET_LANG}/")
                dst = os.path.join(out_root, "assets", rel_ru)
                generic_json.translate_generic_json_file(src_path, dst, translator)
                processed += 1
                print(f"[OK] patchouli → {dst}")
    return processed


def _scan_tips_in_assets(assets_dir: str, out_root: str, translator, processed: int) -> int:
    """assets/**/tips/*.json → тот же путь"""
    if not os.path.isdir(assets_dir):
        return processed
    for root, _, files in os.walk(assets_dir):
        root_norm = root.replace("\\", "/")
        if "/tips/" in root_norm:
            for fname in files:
                if not fname.endswith(".json"):
                    continue
                src_path = os.path.join(root, fname)
                rel = os.path.relpath(src_path, start=assets_dir).replace("\\", "/")
                dst = os.path.join(out_root, "assets", rel)
                generic_json.translate_generic_json_file(src_path, dst, translator)
                processed += 1
                print(f"[OK] tips → {dst}")
    return processed


def _scan_lang_in_assets(assets_dir: str, out_root: str, translator, processed: int) -> int:
    """assets/*/lang/en_us.json → ru_ru.json"""
    if not os.path.isdir(assets_dir):
        return processed
    for root, _, files in os.walk(assets_dir):
        root_norm = root.replace("\\", "/")
        if root_norm.endswith("/lang") and "assets/" in root_norm:
            for fname in files:
                if fname != "en_us.json":
                    continue
                src_path = os.path.join(root, fname)
                parts = root_norm.split("/")
                try:
                    modid = parts[parts.index("assets") + 1]
                except Exception:
                    modid = "unknown_mod"
                dst = os.path.join(out_root, "assets", modid, "lang", f"{config.TARGET_LANG}.json")
                lang_json.translate_lang_json(src_path, dst, translator)
                processed += 1
                print(f"[OK] lang → {dst}")
    return processed


def build_resource_pack(base_input: str, out_root: str, translator) -> None:
    """
    Собирает ресурс-пак с переводом:
      1) assets/*/lang/en_us.json из mods/*.jar
      2) kubejs/assets/**/lang/en_us.json на диске
      3) assets/**/patchouli_books/**/en_us/**.json
      4) assets/**/tips/*.json
      5) то же самое внутри config/openloader/resources/**/assets/**
      6) то же самое внутри overrides/kubejs/assets/**
    """
    # аккуратно разворачиваем ~
    base_input = os.path.abspath(os.path.expanduser(base_input))
    out_root = os.path.abspath(out_root)
    os.makedirs(out_root, exist_ok=True)

    # pack.mcmeta
    mcmeta = {
        "pack": {
            "pack_format": 18,  # 1.20.1
            "description": "Автоматический русификатор модов (AI)"
        }
    }
    ensure_dir_for_file(os.path.join(out_root, "pack.mcmeta"))
    with open(os.path.join(out_root, "pack.mcmeta"), "w", encoding="utf-8") as f:
        json.dump(mcmeta, f, ensure_ascii=False, indent=2)

    processed = 0

    # --- 1) mods/*.jar: assets/*/lang/en_us.json ---
    mods_dir = os.path.join(base_input, "mods")
    if os.path.isdir(mods_dir):
        for name in os.listdir(mods_dir):
            if not name.lower().endswith(".jar"):
                continue
            jar_path = os.path.join(mods_dir, name)
            try:
                with zipfile.ZipFile(jar_path, "r") as zf:
                    for info in zf.infolist():
                        p = info.filename.replace("\\", "/")
                        if "/assets/" in p and p.endswith("/lang/en_us.json"):
                            parts = p.split("/")
                            try:
                                modid = parts[parts.index("assets") + 1]
                            except Exception:
                                modid = "unknown_mod"
                            try:
                                data = json.loads(zf.read(p).decode("utf-8"))
                            except Exception:
                                continue
                            dst = os.path.join(out_root, "assets", modid, "lang", f"{config.TARGET_LANG}.json")
                            lang_json.translate_lang_obj(data, dst, translator)
                            processed += 1
                            print(f"[OK] {modid} → {dst}")
            except zipfile.BadZipFile:
                continue

    # --- 2) kubejs/assets/**/lang/en_us.json (на диске) ---
    kube_root = os.path.join(base_input, "kubejs", "assets")
    if os.path.isdir(kube_root):
        for root, _, files in os.walk(kube_root):
            for fname in files:
                if fname == "en_us.json" and "/lang/" in root.replace("\\", "/"):
                    src_path = os.path.join(root, fname)
                    parts = src_path.replace("\\", "/").split("/")
                    try:
                        idx = parts.index("assets")
                        modid = parts[idx + 1]
                    except Exception:
                        modid = "kubejs"
                    dst = os.path.join(out_root, "assets", modid, "lang", f"{config.TARGET_LANG}.json")
                    lang_json.translate_lang_json(src_path, dst, translator)
                    processed += 1
                    print(f"[OK] kubejs:{modid} → {dst}")

    # --- 3-4) assets/ в корне сборки ---
    assets_dir = os.path.join(base_input, "assets")
    processed = _scan_patchouli_in_assets(assets_dir, out_root, translator, processed)
    processed = _scan_tips_in_assets(assets_dir, out_root, translator, processed)
    processed = _scan_lang_in_assets(assets_dir, out_root, translator, processed)

    # --- 5) assets/ внутри OpenLoader: config/openloader/resources/**/assets ---
    ol_root = os.path.join(base_input, "config", "openloader", "resources")
    if os.path.isdir(ol_root):
        for root, dirs, _ in os.walk(ol_root):
            if os.path.basename(root) == "assets":
                processed = _scan_patchouli_in_assets(root, out_root, translator, processed)
                processed = _scan_tips_in_assets(root, out_root, translator, processed)
                processed = _scan_lang_in_assets(root, out_root, translator, processed)

    # --- 6) assets/ внутри overrides/kubejs: overrides/kubejs/assets ---
    overrides_kube_assets = os.path.join(base_input, "overrides", "kubejs", "assets")
    processed = _scan_patchouli_in_assets(overrides_kube_assets, out_root, translator, processed)
    processed = _scan_tips_in_assets(overrides_kube_assets, out_root, translator, processed)
    processed = _scan_lang_in_assets(overrides_kube_assets, out_root, translator, processed)

    print(f"[DONE] Переведено источников: {processed}")
