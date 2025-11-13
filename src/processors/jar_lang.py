# src/processors/jar_lang.py
from __future__ import annotations
import os
import io
import json
import zipfile
from typing import Callable, Optional

from .. import config
from ..utils.helpers import ensure_dir_for_file


def _translate_lang_dict(data: dict, translator, target_lang: str) -> dict:
    out = {}
    for k, v in data.items():
        if isinstance(v, str):
            try:
                out[k] = translator.translate(v, target_lang=target_lang)
            except Exception:
                # если переводчик упал на конкретной строке — оставим оригинал
                out[k] = v
        else:
            out[k] = v
    return out


def process_jar_lang(
    jar_path: str,
    out_root: str,
    translator,
    log: Callable[[str], None] = print,
    write: bool = True,
    target_lang: Optional[str] = None,
) -> int:
    """
    Ищет внутри JAR файлы assets/**/lang/en_us.json и
    пишет переводы в out_root, зеркально меняя en_us.json → ru_ru.json.
    Возвращает число успешно обработанных файлов.
    """
    target_lang = target_lang or config.TARGET_LANG
    jar_path = os.path.abspath(jar_path)
    out_root = os.path.abspath(out_root)

    processed = 0

    # пример члена архива: assets/modid/lang/en_us.json
    def _is_en_us_lang(member: str) -> bool:
        m = member.replace("\\", "/")
        return m.startswith("assets/") and m.endswith("/lang/en_us.json")

    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            members = [m for m in zf.namelist() if _is_en_us_lang(m)]
            if not members:
                return 0

            for member in members:
                try:
                    raw = zf.read(member)
                except KeyError:
                    # внезапно нет — пропустим
                    continue

                # читаем JSON
                try:
                    data = json.loads(raw.decode("utf-8"))
                    if not isinstance(data, dict):
                        log(f"[LWRN][jar] {os.path.basename(jar_path)}:{member}: not an object, skip")
                        continue
                except Exception as e:
                    log(f"[LWRN][jar] {os.path.basename(jar_path)}:{member}: bad json: {e}")
                    continue

                # перевод
                translated = _translate_lang_dict(data, translator, target_lang)

                # путь вывода: out_root/jar/<jarname>/<assets/.../lang/ru_ru.json>
                jarname = os.path.splitext(os.path.basename(jar_path))[0]
                rel_ru = member.replace("/en_us.json", f"/{target_lang}.json")
                dst_path = os.path.join(out_root, "jar_lang", jarname, rel_ru)

                if write:
                    ensure_dir_for_file(dst_path)
                    with open(dst_path, "w", encoding="utf-8", newline="\n") as f:
                        json.dump(translated, f, ensure_ascii=False, indent=2)
                    log(f"[OK][jar] {os.path.basename(jar_path)}:{member} → {os.path.relpath(dst_path, out_root)}")
                else:
                    log(f"[dry][jar] {os.path.basename(jar_path)}:{member} → {rel_ru}")

                processed += 1

    except zipfile.BadZipFile:
        log(f"[LWRN][jar] {os.path.basename(jar_path)}: BadZipFile, skip")
    except Exception as e:
        log(f"[ERR][jar] {os.path.basename(jar_path)}: {e}")

    return processed
