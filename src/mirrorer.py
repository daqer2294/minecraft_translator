# src/mirrorer.py
from __future__ import annotations
import os
import json
import re
from typing import Callable, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from zipfile import ZipFile, BadZipFile

from .processors import lang_json, generic_json, ftb_snbt, kubejs_js, jar_lang
from .processors.snbt_structured import translate_snbt_file_structured
from .modpack_info import detect_modpack
from .resource_pack import ResourcePackBuilder
from .utils.helpers import ensure_dir_for_file
from . import config, glossary


# Локаль FTB Quests: lang/en_us.snbt или lang/en_us/chapters/x.snbt
_FTB_LANG_RE = re.compile(r"/ftbquests/(?:[^/]+/)*?lang/([A-Za-z]{2,3}_[A-Za-z]{2,4})(?=\.snbt$|/)")
_FTB_SOURCE_LOCALE = "en_us"


def _rel(base: str, p: str) -> str:
    return os.path.relpath(p, start=base).replace("\\", "/")


def _ensure_readable(base_input: str) -> None:
    """
    Явно проверяем, что входную папку МОЖНО читать.

    macOS TCC: если у приложения нет доступа к папке (Documents/Desktop/Downloads
    и т.п.), низкоуровневый opendir возвращает EPERM → os.walk с onerror=None
    молча вернёт ПУСТО, и пользователь видел «scanned: 0» без ошибки. Тут мы
    инициируем реальный доступ и превращаем отказ в понятную ошибку.
    """
    if not os.path.exists(base_input):
        raise FileNotFoundError(f"Папка не найдена: {base_input}")
    try:
        with os.scandir(base_input) as it:
            next(it, None)
    except PermissionError as e:
        raise PermissionError(
            "Нет доступа к папке:\n"
            f"{base_input}\n"
            "Разрешите доступ в «Системные настройки → Конфиденциальность и "
            "безопасность → Файлы и папки» (или «Доступ к диску»), либо выберите "
            "папку заново кнопкой «Выбрать…» и повторите."
        ) from e


def _goes_to_pack(rel: str) -> bool:
    """KubeJS читает свою папку assets сам, поэтому его файлы остаются на своём пути."""
    return not (rel.startswith("kubejs/") or "/kubejs/" in rel)


def _pack_rel(path_norm: str) -> str:
    """Путь внутри ресурспака: от последней папки assets."""
    return "assets/" + path_norm.rsplit("/assets/", 1)[1]


def _is_lang_en_us(path_norm: str) -> bool:
    return path_norm.endswith("/lang/en_us.json") and "/assets/" in path_norm


def _is_patchouli_json(path_norm: str) -> bool:
    return (
        "/assets/" in path_norm
        and "/patchouli_books/" in path_norm
        and "/en_us/" in path_norm
        and path_norm.endswith(".json")
    )


def _is_tips_json(path_norm: str) -> bool:
    return "/assets/" in path_norm and "/tips/" in path_norm and path_norm.endswith(".json")


def _want_ftb_snbt(path_norm: str) -> bool:
    if "/ftbquests/" not in path_norm or not path_norm.endswith(".snbt"):
        return False
    # Источник перевода только en_us: чужие локали и готовый перевод сборки не трогаем.
    locale = _FTB_LANG_RE.search(path_norm)
    return locale is None or locale.group(1).lower() == _FTB_SOURCE_LOCALE


def _ftb_dst_rel(rel: str, path_norm: str, target_lang: str) -> str:
    """lang/en_us... → lang/<target_lang>...; остальные .snbt сохраняют свой путь."""
    locale = _FTB_LANG_RE.search(path_norm)
    if locale is None:
        return rel
    start = locale.start(1) - (len(path_norm) - len(rel))
    if start < 0:  # выбрана сама папка en_us: имени локали в относительном пути нет
        return rel
    return rel[:start] + target_lang + rel[locale.end(1) - (len(path_norm) - len(rel)):]


def _want_kubejs_js(path_norm: str) -> bool:
    return "/kubejs/" in path_norm and path_norm.endswith(".js")


# Папки, откуда игра и OpenLoader берут паки ресурсов. config/resourcepacks игра не читает.
_RESOURCE_ZIP_DIRS = ("resourcepacks", "config/openloader/resources", "config/openloader/packs")


def _is_resource_zip(rel: str) -> bool:
    folder, _, name = rel.replace("\\", "/").rpartition("/")
    return name.lower().endswith(".zip") and folder in _RESOURCE_ZIP_DIRS


def _is_candidate(path_norm: str) -> bool:
    return (
        _is_lang_en_us(path_norm)
        or _is_patchouli_json(path_norm)
        or _is_tips_json(path_norm)
        or _want_ftb_snbt(path_norm)
        or _want_kubejs_js(path_norm)
    )


def _error_text(e: Exception) -> str:
    """У части исключений пустой текст: тогда в логе хотя бы тип."""
    return str(e) or type(e).__name__


def _cancelled(cancel_event) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _discard_if_cancelled(dst: str, cancel_event, log: Callable[[str], None]) -> bool:
    """
    P0-2: файл мог быть уже «в полёте», когда нажали «Стоп». Тогда translator
    вернул ИСХОДНИКИ (отмена), и процессор записал непереведённый файл. Такой
    результат нельзя оставлять в out_root: при следующем запуске _dst_exists
    посчитал бы его готовым и навсегда пропустил. Удаляем — прогон переделает.
    """
    if not _cancelled(cancel_event):
        return False
    try:
        if os.path.exists(dst):
            os.remove(dst)
    except OSError:
        pass
    log(f"[STOP][discard] {os.path.basename(dst)} — прерван, не сохранён")
    return True


def _ftb_official_path(src_path: str, path_norm: str, target_lang: str) -> Optional[str]:
    """Готовый перевод этого файла в самой сборке: lang/en_us... → lang/<target_lang>..."""
    locale = _FTB_LANG_RE.search(path_norm)
    if locale is None or locale.group(1).lower() != _FTB_SOURCE_LOCALE:
        return None
    candidate = path_norm[:locale.start(1)] + target_lang + path_norm[locale.end(1):]
    return candidate if os.path.isfile(candidate) else None


def _dst_exists(out_root: str, rel: str, target_lang: str, path_norm: str) -> bool:
    # Пак собирается заново при каждом запуске, готовые строки берутся из кэша.
    if "/assets/" in path_norm and not rel.endswith((".js", ".snbt")) and _goes_to_pack(rel):
        return False
    if rel.endswith("/lang/en_us.json"):
        dst_ru = os.path.join(out_root, os.path.dirname(rel), f"{target_lang}.json")
        return os.path.exists(dst_ru)
    if "/patchouli_books/" in rel and "/en_us/" in rel:
        rel_ru = rel.replace("/en_us/", f"/{target_lang}/")
        return os.path.exists(os.path.join(out_root, rel_ru))
    if "/tips/" in rel and rel.endswith(".json"):
        return os.path.exists(os.path.join(out_root, rel))
    if rel.endswith(".snbt"):
        return os.path.exists(os.path.join(out_root, _ftb_dst_rel(rel, path_norm, target_lang)))
    if rel.endswith(".js"):
        return os.path.exists(os.path.join(out_root, rel))
    return False


def _process_file(
    base_input: str,
    out_root: str,
    src_path: str,
    translator,
    write: bool,
    log: Callable[[str], None],
    target_lang: str,
    cancel_event=None,
    pack: Optional[ResourcePackBuilder] = None,
    force: bool = False,
) -> Tuple[bool, bool, bool]:
    """
    Возвращает (matched, ok, skipped)
      matched  — файл относится к поддерживаемым типам
      ok       — обработан без ошибок (включая SKIP как True)
      skipped  — был пропущен из-за уже существующего dst
    """
    # P0-2: воркер мог быть запущен ДО «Стоп». Проверяем отмену до любой работы и,
    # главное, до записи на диск — иначе в out_root попадают непереведённые файлы.
    if _cancelled(cancel_event):
        return False, False, False

    rel = _rel(base_input, src_path)
    path_norm = src_path.replace("\\", "/")

    if not _is_candidate(path_norm):
        return False, False, False

    # уже готов — считаем как matched+ok+skipped
    if write and not force and _dst_exists(out_root, rel, target_lang, path_norm):
        log(f"[SKIP][exists] {rel}")
        return True, True, True

    # 1) lang/en_us.json → ru_ru.json
    if _is_lang_en_us(path_norm):
        dst_ru = os.path.join(out_root, os.path.dirname(rel), f"{target_lang}.json")
        try:
            if write:
                official = os.path.join(os.path.dirname(src_path), f"{target_lang}.json")
                merged, fresh, official_keys = lang_json.translate_lang_file(src_path, translator, official, target_lang)
                if _discard_if_cancelled(dst_ru, cancel_event, log):
                    return False, False, False
                if merged is not None:
                    if _goes_to_pack(rel):
                        lang_rel = _pack_rel(path_norm).replace("/en_us.json", f"/{target_lang}.json")
                        pack.add_lang(lang_rel, merged, official_keys)
                    else:
                        lang_json.write_lang(dst_ru, merged)
                    log(f"[OK][lang] {rel}: новых {fresh}")
                else:
                    log(f"[SKIP][official] {rel}: новых переводов нет")
            else:
                with open(src_path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                vals = [v for v in data.values() if isinstance(v, str)]
                if vals:
                    translator.translate_many(vals, target_lang=target_lang)
                log(f"[DRY][lang] {rel}")
            return True, True, False
        except Exception as e:
            log(f"[ERR][lang] {rel}: {_error_text(e)}")
            return True, False, False

    # 2) patchouli en_us → ru_ru
    if _is_patchouli_json(path_norm):
        rel_ru = rel.replace("/en_us/", f"/{target_lang}/")
        dst_ru = os.path.join(out_root, rel_ru)
        if os.path.isfile(src_path.replace("/en_us/", f"/{target_lang}/")):
            log(f"[SKIP][official] {rel}: страница уже переведена")
            return True, True, True
        try:
            if write:
                if _goes_to_pack(rel):
                    result = generic_json.translate_generic_json(src_path, translator)
                    if _discard_if_cancelled(dst_ru, cancel_event, log):
                        return False, False, False
                    pack.add_json(_pack_rel(path_norm).replace("/en_us/", f"/{target_lang}/"), result)
                else:
                    os.makedirs(os.path.dirname(dst_ru), exist_ok=True)
                    generic_json.translate_generic_json_file(src_path, dst_ru, translator)
                    if _discard_if_cancelled(dst_ru, cancel_event, log):
                        return False, False, False
                log(f"[OK][patchouli] {rel}")
            else:
                with open(src_path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)

                acc: List[str] = []

                def collect(n):
                    if isinstance(n, str):
                        acc.append(n)
                        return
                    if isinstance(n, list):
                        for i in n:
                            collect(i)
                    elif isinstance(n, dict):
                        for v in n.values():
                            collect(v)

                collect(data)
                if acc:
                    translator.translate_many(acc, target_lang=target_lang)
                log(f"[DRY][patchouli] {rel}")
            return True, True, False
        except Exception as e:
            log(f"[ERR][patchouli] {rel}: {_error_text(e)}")
            return True, False, False

    # 3) tips/*.json → тот же путь
    if _is_tips_json(path_norm):
        dst = os.path.join(out_root, rel)
        try:
            if write:
                if _goes_to_pack(rel):
                    result = generic_json.translate_generic_json(src_path, translator)
                    if _discard_if_cancelled(dst, cancel_event, log):
                        return False, False, False
                    pack.add_json(_pack_rel(path_norm), result)
                else:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    generic_json.translate_generic_json_file(src_path, dst, translator)
                    if _discard_if_cancelled(dst, cancel_event, log):
                        return False, False, False
                log(f"[OK][tips] {rel}")
            else:
                with open(src_path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)

                acc: List[str] = []

                def collect(n):
                    if isinstance(n, str):
                        acc.append(n)
                        return
                    if isinstance(n, list):
                        for i in n:
                            collect(i)
                    elif isinstance(n, dict):
                        for v in n.values():
                            collect(v)

                collect(data)
                if acc:
                    translator.translate_many(acc, target_lang=target_lang)
                log(f"[DRY][tips] {rel}")
            return True, True, False
        except Exception as e:
            log(f"[ERR][tips] {rel}: {_error_text(e)}")
            return True, False, False

    # 4) FTB Quests .snbt  — новый структурный перевод + запасной старый
    if _want_ftb_snbt(path_norm):
        dst = os.path.join(out_root, _ftb_dst_rel(rel, path_norm, target_lang))
        try:
            if write:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                official = _ftb_official_path(src_path, path_norm, target_lang)
                try:
                    # новый путь: SNBT → NBT → рекурсивный перевод → SNBT
                    if not translate_snbt_file_structured(src_path, dst, translator, official):
                        log(f"[SKIP][official] {rel}: всё переведено официально")
                        return True, True, True
                    log(f"[OK][snbt-struct] {rel}")
                except Exception as e_struct:
                    if official:
                        # regex-проход не умеет сливать, а затереть готовый перевод нельзя
                        log(f"[ERR][snbt] {rel}: {_error_text(e_struct)}")
                        return True, False, False
                    # запасной вариант — старый regex-проход, чтобы не потерять файл
                    log(f"[WARN][snbt-struct] {rel}: {e_struct} → fallback ftb_snbt")
                    with open(src_path, "r", encoding="utf-8-sig") as f:
                        text = f.read()
                    out = ftb_snbt.translate_ftb_snbt_text(text, translator)
                    with open(dst, "w", encoding="utf-8", newline="\n") as f:
                        f.write(out)
                    log(f"[OK][snbt] {rel}")
                if _discard_if_cancelled(dst, cancel_event, log):
                    return False, False, False
            else:
                log(f"[DRY][snbt] {rel}")
            return True, True, False
        except Exception as e:
            log(f"[ERR][snbt] {rel}: {_error_text(e)}")
            return True, False, False

    # 5) KubeJS .js
    if _want_kubejs_js(path_norm):
        dst = os.path.join(out_root, rel)
        try:
            if write:
                changed = kubejs_js.translate_kubejs_script_file(src_path, dst, translator)
                if _discard_if_cancelled(dst, cancel_event, log):
                    return False, False, False
                if not changed:
                    log(f"[SKIP][unchanged] {rel}")
                    return True, True, True
                log(f"[OK][kubejs] {rel}")
            else:
                log(f"[DRY][kubejs] {rel}")
            return True, True, False
        except Exception as e:
            log(f"[ERR][kubejs] {rel}: {_error_text(e)}")
            return True, False, False

    return False, False, False


def _jar_has_lang_en_us(jar_path: str, include_nested: bool = False) -> bool:
    if not config.SCAN_JAR_LANG:
        return True
    try:
        with ZipFile(jar_path) as zf:
            return jar_lang.jar_has_lang(zf, include_nested)
    except BadZipFile:
        return False


def _second_pass(translator, strong_client, log, on_total, on_tick, cancel_event) -> int:
    """
    Отправляет сильной модели только строки с браком основной. Ответ проходит тот же
    валидатор, брак снова не кэшируется. Возвращает число исправленных строк.
    """
    pending = translator.take_rejected()
    if not pending:
        return 0
    client, reason = strong_client()
    if client is None:
        log(f"[PASS2] Пропущен: {len(pending)} строк с браком остаются на английском. {reason}")
        return 0

    log(f"[PASS2] Брак основной модели в {len(pending)} строках. Второй проход: {client.name}")
    if on_total:
        on_total(len(pending))
    fixed = 0
    for text, lang in pending:
        if _cancelled(cancel_event):
            return fixed
        translated = translator.translate(text, lang, client=client)
        if _cancelled(cancel_event):
            return fixed
        good = translated != text
        fixed += good
        if on_tick:
            on_tick(*((1, 1, 0, 0) if good else (1, 0, 0, 1)))
    translator.take_rejected()
    log(f"[PASS2] Исправлено {fixed} из {len(pending)}, на английском осталось {len(pending) - fixed}.")
    return fixed


def _rebuild_from_cache(
    base_input, out_root, candidates, jars, translator, log, target_lang, cancel_event, pack_format, include_nested,
) -> Optional[ResourcePackBuilder]:
    """Заново собирает вывод из кэша, без запросов к модели. None: прервано «Стоп»."""

    def errors_only(line: str) -> None:
        if line.startswith(("[ERR", "[LWRN", "[WARN")):
            log(line)

    pack = ResourcePackBuilder(out_root, target_lang, pack_format)
    translator.cache_only = True
    try:
        for path in candidates:
            if _cancelled(cancel_event):
                return None
            _process_file(
                base_input, out_root, path, translator, True, errors_only,
                target_lang, cancel_event, pack, force=True,
            )
        for jar in jars:
            if _cancelled(cancel_event):
                return None
            jar_lang.process_jar_lang(
                jar, pack, translator, log=errors_only, write=True, target_lang=target_lang,
                cancel_event=cancel_event, include_nested=include_nested,
            )
    finally:
        translator.cache_only = False
    return None if _cancelled(cancel_event) else pack


def mirror_translate_dir(
    base_input: str,
    out_root: str,
    translator,
    log: Callable[[str], None] = print,
    write: bool = True,
    on_total: Optional[Callable[[int], None]] = None,
    on_tick: Optional[Callable[[int, int, int, int], None]] = None,  # (done, ok, err, skip)
    cancel_event=None,
    target_lang: Optional[str] = None,
    strong_client: Optional[Callable[[], tuple]] = None,
) -> None:
    """
    Параллельный проход по кандидатам с визуализацией прогресса.

    cancel_event — «Стоп»: отменяет ещё не начатые файлы (они НЕ пишутся в out_root).
    target_lang  — снимок языка на весь прогон (P0-3); None → берётся из config
                   один раз здесь, а не читается воркерами на лету.
    strong_client — вызывается только при браке основной модели и возвращает
                   (клиент сильной модели или None, причина). None: второго прохода нет.
    """
    try:
        _mirror_translate_dir(
            base_input, out_root, translator, log, write, on_total, on_tick, cancel_event, target_lang,
            strong_client,
        )
    finally:
        # При любом выходе, включая _StopRequested из on_tick: сделанное не должно пропасть.
        try:
            translator.flush()
        except Exception:
            pass


def _mirror_translate_dir(
    base_input, out_root, translator, log, write, on_total, on_tick, cancel_event, target_lang,
    strong_client=None,
) -> None:
    base_input = os.path.abspath(os.path.expanduser(base_input))
    out_root = os.path.abspath(out_root)
    target_lang = target_lang or getattr(config, "TARGET_LANG", "ru_ru")

    # Доступ к входной папке — до скана (иначе os.walk молча вернёт пусто).
    _ensure_readable(base_input)

    # 1) Скан: собираем кандидатов (не все файлы подряд)
    candidates: List[str] = []
    jar_files: List[str] = []

    # os.walk по умолчанию МОЛЧА игнорирует ошибки доступа к подпапкам —
    # перехватываем их и сообщаем, чтобы частичный отказ не выглядел как «пусто».
    perm_denied: List[str] = []

    def _on_walk_error(err: OSError):
        target = getattr(err, "filename", None) or str(err)
        perm_denied.append(str(target))
        log(f"[WARN][access] нет доступа: {target} ({err})")

    for root, _, fnames in os.walk(base_input, onerror=_on_walk_error):
        r_norm = root.replace("\\", "/")
        for fname in fnames:
            full = os.path.join(root, fname)
            pnorm = full.replace("\\", "/")
            # jar внутри папки mods/ — проверяем ПОЛНЫЙ путь (pnorm), а НЕ r_norm:
            # для jar прямо в <base>/mods/ каталог = ".../mods" БЕЗ хвостового «/»,
            # поэтому "/mods/" in r_norm было False → все моды CurseForge молча
            # игнорировались (jar лежат прямо в mods/, не в подпапках).
            if fname.endswith(".jar") and "/mods/" in pnorm:
                jar_files.append(full)
                continue
            if _is_resource_zip(_rel(base_input, full)):
                jar_files.append(full)
                continue
            if _is_candidate(pnorm):
                candidates.append(full)

    modpack = detect_modpack(base_input)
    log(f"[PACK-INFO] {modpack.describe()}, формат ресурспака {modpack.pack_format}")
    for warning in modpack.warnings:
        log(f"[WARN][pack-info] {warning}")
    # META-INF/jars читает только загрузчик Fabric (и Quilt). Если загрузчик неизвестен,
    # такие jar всё равно есть только у модов Fabric, а jarjar NeoForge лежит в другой папке.
    include_nested = modpack.loader in (None, "fabric", "quilt")

    # 2) Предскан JAR'ов
    jar_ready: List[str] = []
    for jp in jar_files:
        if _jar_has_lang_en_us(jp, include_nested):
            jar_ready.append(jp)

    # Словарь терминов: только то, что официально переведено в самой сборке
    if config.GLOSSARY_ENABLED and hasattr(translator, "set_glossary"):
        glossary_data = glossary.build_glossary(jar_ready, target_lang, base_input, modpack.minecraft or "")
        translator.set_glossary(glossary_data)
        log(f"[GLOSSARY] Форм терминов: {glossary_data.size}")

    total = len(candidates) + len(jar_ready)

    # Ничего не нашли — объясняем ПОЧЕМУ (доступ vs пустая/не та папка), а не
    # молча «scanned: 0».
    if total == 0:
        if perm_denied:
            log(
                "[!] Ничего не отсканировано: часть папок недоступна (нет прав). "
                "Разрешите доступ к папке в «Системные настройки → Конфиденциальность "
                "и безопасность → Файлы и папки» и повторите."
            )
        else:
            log(
                "[!] В выбранной папке не найдено поддерживаемых файлов "
                "(assets/*/lang/en_us.json, FTB Quests .snbt, Patchouli, tips, "
                "KubeJS .js, .jar в mods). Проверьте, что выбрана папка модпака."
            )

    if on_total:
        on_total(total)

    matched = 0
    ok = 0
    skipped = 0
    err = 0
    pack = ResourcePackBuilder(out_root, target_lang, modpack.pack_format) if write else None
    # Файлы, готовые с прошлого запуска, второй проход не переписывает
    finished_before = {
        p for p in candidates
        if write and _dst_exists(out_root, _rel(base_input, p), target_lang, p.replace("\\", "/"))
    }

    # 3) Параллельно обрабатываем кандидатов
    def tick(matched_inc: int, ok_inc: int, err_inc: int, skip_inc: int):
        nonlocal matched, ok, skipped, err
        matched += matched_inc
        ok += ok_inc
        skipped += skip_inc
        err += err_inc
        if on_tick:
            on_tick(matched_inc, ok_inc, err_inc, skip_inc)

    # P0-2: НЕ используем `with ThreadPoolExecutor(...)` — его __exit__ делает
    # shutdown(wait=True) БЕЗ cancel_futures, из-за чего при «Стоп» вся уже
    # поставленная очередь всё равно выполнялась и писала непереведённые файлы.
    # Здесь на выходе (в т.ч. по _StopRequested из on_tick) отменяем всё, что ещё
    # не начали исполнять.
    ex = ThreadPoolExecutor(max_workers=getattr(config, "MAX_WORKERS_FILES", 6))
    try:
        futs = [
            ex.submit(
                _process_file, base_input, out_root, p, translator, write, log,
                target_lang, cancel_event, pack,
            )
            for p in candidates
        ]
        for f in as_completed(futs):
            # отмена без on_tick (напр. CLI): выходим сами
            if _cancelled(cancel_event):
                break
            try:
                m, good, skip = f.result()
            except Exception as e:
                log(f"[ERR] worker: {_error_text(e)}")
                tick(1, 0, 1, 0)
                continue
            if m:
                if skip:
                    tick(1, 0, 0, 1)
                elif good:
                    tick(1, 1, 0, 0)
                else:
                    tick(1, 0, 1, 0)
            else:
                tick(1, 0, 0, 0)
    finally:
        # cancel_futures=True — ключ к настоящей отмене: задачи, которые ещё не
        # стартовали, не выполнятся вовсе. Уже бегущие (≤ max_workers) дорабатывают.
        ex.shutdown(wait=True, cancel_futures=True)

    # Остановлено пользователем — до JAR-ов не идём.
    if _cancelled(cancel_event):
        log("[STOP] Остановлено: оставшиеся файлы не обработаны и не записаны.")
        return

    # 4) Последовательно JAR-моды
    # R-1: раньше здесь вызывался несуществующий jar_lang.process_jar(...) с
    #      неправильной сигнатурой → перевод lang внутри .jar всегда падал в
    #      except. Корректная функция — process_jar_lang(jar_path, out_root, ...).
    for jp in jar_ready:
        if _cancelled(cancel_event):
            log("[STOP] Остановлено: оставшиеся .jar не обработаны.")
            break
        # tick вне try: он бросает _StopRequested, и это не ошибка обработки jar.
        try:
            worked = jar_lang.process_jar_lang(
                jp,
                pack,
                translator,
                log=log,
                write=write,
                target_lang=target_lang,
                cancel_event=cancel_event,
                include_nested=include_nested,
            )
        except Exception as e:
            log(f"[ERR][jar] {os.path.basename(jp)}: {_error_text(e)}")
            result = (1, 0, 1, 0)
        else:
            result = (1, 1, 0, 0) if worked else (1, 0, 0, 1)
        if _cancelled(cancel_event):
            break
        tick(*result)

    # «Стоп» в цикле jar: пак не собираем, повторный запуск возьмёт строки из кэша.
    if _cancelled(cancel_event):
        log("[STOP] Ресурспак не собран: запустите перевод снова, готовые строки сохранены в кэше.")
        return

    # 5) Второй проход: брак основной модели переводит сильная
    if write and strong_client is not None:
        fixed = _second_pass(translator, strong_client, log, on_total, on_tick, cancel_event)
        if _cancelled(cancel_event):
            log("[STOP] Ресурспак не собран: запустите перевод снова, готовые строки сохранены в кэше.")
            return
        if fixed:
            pack = _rebuild_from_cache(
                base_input, out_root, [p for p in candidates if p not in finished_before], jar_ready,
                translator, log, target_lang, cancel_event, modpack.pack_format, include_nested,
            )
            if pack is None:
                log("[STOP] Ресурспак не собран: запустите перевод снова, готовые строки сохранены в кэше.")
                return

    if pack is not None:
        zip_path = pack.finish()
        if zip_path:
            s = pack.stats()
            log(
                f"[PACK] {zip_path}: lang-файлов {s['lang_files']}, строк {s['strings']}, "
                f"других файлов {s['other_files']}. Положите zip в папку resourcepacks сборки "
                f"и включите его в игре. Папки config и kubejs из результата скопируйте в папку сборки."
            )

    status = "DONE (dry-run)" if not write else "DONE"
    log(
        f"[{status}] total: {total}, matched: {matched}, ok: {ok}, "
        f"skip: {skipped}, err: {err}, jars: {len(jar_ready)} / scanned: {len(jar_files)}"
    )
