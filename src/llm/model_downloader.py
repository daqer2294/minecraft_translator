# src/llm/model_downloader.py
from __future__ import annotations

import errno
import os
import ssl
import threading
import urllib.error
import urllib.request
from typing import Callable, Optional

import certifi

from .. import config
from . import model_registry as registry
from .model_registry import ModelSpec


def _ssl_context() -> Optional[ssl.SSLContext]:
    """
    SSL-контекст с CA-бандлом certifi.

    Критично для PyInstaller-сборок (особенно macOS/.app): у «замороженного»
    Python нет доступа к системным CA, и SSL-верификация HTTPS к HuggingFace
    падает/висит. Явно указываем certifi.where() — так же, как в
    openai_compatible.py. Если что-то пошло не так — возвращаем None (urllib
    возьмёт дефолтный контекст), чтобы не ломать dev-окружение.
    """
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None

# =============================================================================
# Скачивание GGUF-моделей с HuggingFace по требованию.
#
# Прогресс отдаётся через callback progress_cb(downloaded_bytes, total_bytes) —
# тот же паттерн «колбэк прогресса», что и on_total/on_tick в mirrorer.py, чтобы
# GUI мог рисовать прогрессбар, не блокируя UI-поток (см. download_model_async).
#
# URL резолвим через huggingface_hub.hf_hub_url (если установлен), иначе строим
# стандартный resolve-URL. Само скачивание — потоковое через urllib (stdlib),
# чтобы отдавать прогресс и не тащить лишних зависимостей в момент загрузки.
# =============================================================================

# коллбэк прогресса: (скачано_байт, всего_байт|0_если_неизвестно)
ProgressCB = Callable[[int, int], None]

_CHUNK = 1 << 20        # 1 MiB
_HTTP_TIMEOUT = 30      # сек на установление/чтение
_SIZE_TOLERANCE = 0.10  # допускаемое отклонение размера от реестрового


class DownloadError(Exception):
    """Понятная пользователю ошибка скачивания (сеть/диск/HF/целостность)."""


def local_model_path(spec: ModelSpec) -> str:
    """Куда кладём файл модели: <MODELS_DIR>/<model_id>/<filename>."""
    return os.path.join(config.MODELS_DIR, spec.id, spec.hf_filename)


def is_downloaded(spec: ModelSpec, tolerance: float = _SIZE_TOLERANCE) -> bool:
    """Скачана ли модель (файл есть и размер не слишком мал)."""
    path = local_model_path(spec)
    if not os.path.exists(path):
        return False
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if spec.size_mb <= 0:
        return size > 0
    expected = spec.size_mb * 1024 * 1024
    return size >= expected * (1.0 - tolerance)


def _resolve_url(spec: ModelSpec) -> str:
    """URL файла на HF. Предпочитаем huggingface_hub, иначе — прямой resolve-URL."""
    try:
        from huggingface_hub import hf_hub_url  # type: ignore
        return hf_hub_url(repo_id=spec.hf_repo, filename=spec.hf_filename)
    except Exception:
        # Стандартный публичный resolve-URL HuggingFace.
        return f"https://huggingface.co/{spec.hf_repo}/resolve/main/{spec.hf_filename}"


def _cleanup(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def download_model(
    spec: ModelSpec,
    progress_cb: Optional[ProgressCB] = None,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """
    Скачать модель в local_model_path(spec). Возвращает путь к файлу.

    Ошибки (нет интернета, HF недоступен/404, диск заполнен, отмена, неполный
    файл) → DownloadError с понятным сообщением. Приложение не должно падать —
    вызывающий ловит DownloadError.
    """
    config.ensure_app_dirs()
    target = local_model_path(spec)
    part = target + ".part"

    # уже на месте
    if is_downloaded(spec):
        return target

    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
    except OSError as e:
        raise DownloadError(f"Не удалось создать папку для модели: {e}") from e

    url = _resolve_url(spec)
    req = urllib.request.Request(url, headers={"User-Agent": "minecraft_translator"})
    ctx = _ssl_context()

    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=ctx) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            # Сразу сообщаем известный размер — чтобы UI показал «0 / N MB», а не
            # «0 MB / ?». Если этот колбэк не сработал, значит заголовки так и не
            # пришли (сеть/SSL), и это будет видно (ошибка ниже), а не тихий висяк.
            if progress_cb:
                try:
                    progress_cb(0, total)
                except Exception:
                    pass
            with open(part, "wb") as f:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadError("Загрузка отменена пользователем.")
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        try:
                            progress_cb(downloaded, total)
                        except Exception:
                            # колбэк GUI не должен ронять загрузку
                            pass
    except DownloadError:
        _cleanup(part)
        raise
    except urllib.error.HTTPError as e:
        _cleanup(part)
        raise DownloadError(
            f"HuggingFace вернул ошибку HTTP {e.code} для "
            f"{spec.hf_repo}/{spec.hf_filename}. Проверьте название модели и доступность."
        ) from e
    except urllib.error.URLError as e:
        _cleanup(part)
        raise DownloadError(
            f"Нет соединения с HuggingFace ({e.reason}). Проверьте интернет/прокси."
        ) from e
    except OSError as e:
        _cleanup(part)
        if getattr(e, "errno", None) == errno.ENOSPC:
            raise DownloadError("Недостаточно места на диске для загрузки модели.") from e
        raise DownloadError(f"Ошибка записи на диск: {e}") from e
    except Exception as e:  # подстраховка — любая иная ошибка
        _cleanup(part)
        raise DownloadError(f"Не удалось скачать модель: {e}") from e

    # ---- проверка целостности (по размеру) ----
    try:
        got = os.path.getsize(part)
    except OSError:
        got = 0

    if total and got < total:
        _cleanup(part)
        raise DownloadError(f"Файл скачан не полностью ({got}/{total} байт).")

    if spec.size_mb > 0:
        expected = spec.size_mb * 1024 * 1024
        # слишком маленький файл — почти наверняка это HTML-страница ошибки, а не GGUF
        if got < expected * 0.5:
            _cleanup(part)
            raise DownloadError(
                f"Размер скачанного файла подозрительно мал ({got} байт, "
                f"ожидалось ~{spec.size_mb} MB). Возможно, модель недоступна."
            )

    try:
        os.replace(part, target)
    except OSError as e:
        _cleanup(part)
        raise DownloadError(f"Не удалось сохранить файл модели: {e}") from e

    return target


def download_model_async(
    spec: ModelSpec,
    progress_cb: Optional[ProgressCB] = None,
    done_cb: Optional[Callable[[str], None]] = None,
    error_cb: Optional[Callable[[Exception], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> threading.Thread:
    """
    Скачать модель в отдельном потоке (не блокируя GUI). Колбэки:
      progress_cb(done, total) — прогресс;
      done_cb(path)            — успех;
      error_cb(exc)            — ошибка (DownloadError с понятным текстом).
    Возвращает запущенный daemon-поток.
    """
    def _run():
        try:
            path = download_model(spec, progress_cb=progress_cb, cancel_event=cancel_event)
            if done_cb:
                done_cb(path)
        except Exception as e:
            if error_cb:
                error_cb(e)
            # без error_cb — молча гасим, чтобы не уронить процесс из потока

    t = threading.Thread(target=_run, name=f"dl-{spec.id}", daemon=True)
    t.start()
    return t


def ensure_model(model_id: str) -> str:
    """
    Утилита для CLI/тестов: гарантировать наличие модели по id (скачать при
    необходимости). Возвращает путь. Бросает DownloadError / ValueError.
    """
    spec = registry.get_by_id(model_id)
    if spec is None:
        raise ValueError(f"Неизвестная модель: {model_id}")
    if is_downloaded(spec):
        return local_model_path(spec)
    return download_model(spec)
