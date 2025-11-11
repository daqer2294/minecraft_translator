# src/processors/jar_lang.py
from __future__ import annotations
import io
import json
import os
import zipfile
from typing import Dict, Iterable, Tuple

from ..utils.helpers import ensure_dir_for_file
from .. import config
from . import lang_json


def _iter_jar_lang_entries(jar_path: str) -> Iterable[Tuple[str, str]]:
    """
    Даёт пары (modid, lang_path_inside) для всех assets/*/lang/en_us.json внутри jar.
    Пример: ("botania", "assets/botania/lang/en_us.json")
    """
    with zipfile.ZipFile(jar_path, "r") as zf:
        for name in zf.namelist():
            # ищем только assets/<modid>/lang/en_us.json
            if not name.endswith("lang/en_us.json"):
                continue
            parts = name.split("/")
            try:
                i = parts.index("assets")
            except ValueError:
                continue
            if i + 2 >= len(parts):
                continue
            modid = parts[i + 1]
            if modid and parts[i + 2] == "lang":
                yield modid, name


def _read_json_from_zip(zf: zipfile.ZipFile, name: str) -> Dict:
    with zf.open(name, "r") as fp:
        data = fp.read()
    # пробуем utf-8; если не выйдет — latin-1 (редко)
    try:
        txt = data.decode("utf-8")
    except UnicodeDecodeError:
        txt = data.decode("latin-1")
    return json.loads(txt)


def translate_from_jar(jar_path: str, out_root: str, translator, log=print) -> int:
    """
    Извлекает assets/*/lang/en_us.json из jar и пишет переведённый
    ru_ru.json в overlay-путь:
      <out_root>/overrides/kubejs/assets/<modid>/lang/ru_ru.json

    Возвращает кол-во успешно переведённых модов.
    """
    ok = 0
    jar_path = os.path.abspath(jar_path)
    out_root = os.path.abspath(out_root)

    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            entries = list(_iter_jar_lang_entries(jar_path))
            if not entries:
                return 0
            for modid, lang_name in entries:
                try:
                    data = _read_json_from_zip(zf, lang_name)
                except Exception as e:
                    log(f"[jar][skip] {os.path.basename(jar_path)}::{lang_name}: {e}")
                    continue

                # куда положим overlay
                dst = os.path.join(
                    out_root, "overrides", "kubejs", "assets", modid, "lang", f"{config.TARGET_LANG}.json"
                )
                os.makedirs(os.path.dirname(dst), exist_ok=True)

                # используем наш переводчик lang_json на dict
                try:
                    # lang_json умеет переводить файл -> файл.
                    # здесь у нас dict: сериализуем во временный буфер
                    src_buf = io.StringIO(json.dumps(data, ensure_ascii=False))
                    # микс: прогоним по ключам вручную (без записи на диск)
                    translated = {}
                    for k, v in data.items():
                        if isinstance(v, str):
                            translated[k] = translator.translate(v, target_lang=config.TARGET_LANG)
                        else:
                            translated[k] = v

                    with open(dst, "w", encoding="utf-8", newline="\n") as f:
                        json.dump(translated, f, ensure_ascii=False, indent=2)
                    log(f"[OK][jar] {os.path.basename(jar_path)} → {dst}")
                    ok += 1
                except Exception as e:
                    log(f"[ERR][jar] {os.path.basename(jar_path)}::{lang_name}: {e}")
    except zipfile.BadZipFile:
        # не jar
        return 0
    return ok
