# src/llm/model_downloader.py
from __future__ import annotations

import errno
import os
import re
import socket
import ssl
import sys
import threading
import time
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

_CHUNK = 1 << 18        # 256 KiB — мельче чанк = чаще прогресс и раньше видно сталл
_HTTP_TIMEOUT = 30      # socket-таймаут на каждую операцию (connect И read)
_STALL_TIMEOUT = 30     # watchdog: макс. секунд БЕЗ роста прогресса (ловит «trickle-сталл»)
_MAX_RETRIES = 5        # число повторов с докачкой (Range)
_RETRY_BACKOFF = 2.0    # база экспоненциального backoff между повторами, сек
_SIZE_TOLERANCE = 0.10  # допускаемое отклонение размера от реестрового


def _log(msg: str) -> None:
    """Диагностический вывод в stderr (виден в терминале при запуске .app напрямую)."""
    print(f"[model_downloader] {msg}", file=sys.stderr, flush=True)


class DownloadError(Exception):
    """Понятная пользователю ошибка скачивания (сеть/диск/HF/целостность)."""


class _Retryable(Exception):
    """Внутренняя: попытка оборвалась (сталл/таймаут/обрыв) — можно докачать через Range."""


class _Cancelled(DownloadError):
    """Пользователь отменил загрузку. .part сохраняется для последующей докачки."""


def local_model_path(spec: ModelSpec) -> str:
    """Куда кладём файл модели: <MODELS_DIR>/<model_id>/<filename>."""
    return os.path.join(config.MODELS_DIR, spec.id, spec.hf_filename)


def is_downloaded(spec: ModelSpec, tolerance: float = _SIZE_TOLERANCE) -> bool:
    """
    Скачана ли модель полностью.

    Финальный файл (без .part) создаётся download_model только ПОСЛЕ полной
    загрузки (os.replace из .part), поэтому его наличие + верный размер = готово.

    Приоритет проверки размера:
      1) точный size_bytes (HF Content-Length) — надёжнее всего: полный файл
         совпадает по размеру байт-в-байт (допускаем крошечный дрейф ≤0.1%);
      2) фоллбек — грубый порог по size_mb (если size_bytes не задан).
    """
    path = local_model_path(spec)
    if not os.path.exists(path):
        return False
    try:
        size = os.path.getsize(path)
    except OSError:
        return False

    size_bytes = getattr(spec, "size_bytes", 0) or 0
    if size_bytes > 0:
        # полный файл ≈ точному размеру; частичный (обрыв) заметно меньше
        return size >= int(size_bytes * 0.999)

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


def _sleep_cancelable(seconds: float, cancel_event: Optional[threading.Event]) -> None:
    """Пауза с проверкой отмены (чтобы backoff можно было прервать)."""
    end = time.time() + max(0.0, seconds)
    while time.time() < end:
        if cancel_event is not None and cancel_event.is_set():
            raise _Cancelled("Загрузка отменена пользователем.")
        time.sleep(min(0.5, max(0.0, end - time.time())))


_CONTENT_RANGE_RE = re.compile(r"bytes\s+\d+-\d+/(\d+)", re.I)


def _underlying_socket(resp):
    """Достаём реальный сокет из urllib-ответа (для принудительного разрыва)."""
    try:
        raw = getattr(getattr(resp, "fp", None), "raw", None)
        return getattr(raw, "_sock", None)
    except Exception:
        return None


def _force_disconnect(resp, sock) -> None:
    """
    Принудительно рвём соединение так, чтобы ЗАБЛОКИРОВАННЫЙ в другом потоке
    read()/recv() немедленно разблокировался.

    Ключевой момент: socket.shutdown(SHUT_RDWR) будит висящий recv() (в отличие
    от .close(), который на macOS/Linux заблокированное чтение НЕ прерывает).
    resp.close() здесь НЕ зовём: он обнуляет resp.fp и создаёт гонку с
    просыпающимся read() (AttributeError). Закрытие делает `with resp:` на выходе.
    """
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass


def _download_attempt(url, part, ctx, resume_from, total_hint, read_timeout,
                      stall_timeout, progress_cb, cancel_event, attempt):
    """
    Одна попытка соединения (докачка через Range, если resume_from > 0).
    Возвращает (downloaded, total). Бросает:
      _Cancelled    — пользователь отменил;
      _Retryable    — сталл/таймаут/обрыв (можно повторить с докачкой);
      DownloadError — фатально (404/403 и т.п.).
    """
    headers = {"User-Agent": "minecraft_translator"}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"
    req = urllib.request.Request(url, headers=headers)

    try:
        resp = urllib.request.urlopen(req, timeout=read_timeout, context=ctx)
    except urllib.error.HTTPError as e:
        if e.code == 416:
            # Range за пределами файла → наш .part >= размера файла: считаем полным.
            _log(f"HTTP 416 (range not satisfiable): .part={resume_from}B считаем полным")
            return resume_from, (total_hint or resume_from)
        raise DownloadError(
            f"HuggingFace вернул ошибку HTTP {e.code}. "
            f"Проверьте название модели и доступность."
        ) from e
    except (urllib.error.URLError, socket.timeout, TimeoutError,
            ConnectionError, OSError) as e:
        raise _Retryable(f"не удалось подключиться: {e}") from e

    with resp:
        status = getattr(resp, "status", 200) or 200
        if resume_from > 0 and status == 206:
            mode, downloaded = "ab", resume_from
            m = _CONTENT_RANGE_RE.search(resp.headers.get("Content-Range", ""))
            total = int(m.group(1)) if m else (total_hint or 0)
            _log(f"attempt {attempt}: HTTP 206 докачка c {resume_from}B, total={total}")
        else:
            if resume_from > 0:
                _log(f"attempt {attempt}: сервер вернул {status} вместо 206 — рестарт c 0")
            mode, downloaded = "wb", 0
            total = int(resp.headers.get("Content-Length") or 0) or (total_hint or 0)
            _log(f"attempt {attempt}: HTTP {status} старт c 0, total={total}")

        if progress_cb:
            try:
                progress_cb(downloaded, total)
            except Exception:
                pass

        # --- stall-watchdog: рвём соединение, если прогресс не растёт ---
        # Работает НЕЗАВИСИМО от socket-таймаута (через shutdown будит висящий
        # read), поэтому спасает даже если таймаут не срабатывает в среде .app.
        sock = _underlying_socket(resp)
        last = [time.time()]
        seen = [downloaded]
        stalled = {"flag": False}
        stop = threading.Event()

        def _watchdog():
            while not stop.wait(1.0):
                if time.time() - last[0] > stall_timeout:
                    stalled["flag"] = True
                    _log(f"STALL: нет прогресса {stall_timeout:.0f}s на {seen[0]}B → рву соединение")
                    _force_disconnect(resp, sock)
                    return

        wd = threading.Thread(target=_watchdog, daemon=True)
        wd.start()

        # read1 отдаёт данные по мере поступления (не блокирует до полного чанка) —
        # прогресс обновляется часто, сталл виден быстро.
        reader = getattr(resp, "read1", None) or resp.read
        step = max(1, ((total or 0) // (1024 * 1024)) // 10)  # логируем ~каждые 10%
        next_mb = 0
        try:
            with open(part, mode) as f:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise _Cancelled("Загрузка отменена пользователем.")
                    try:
                        chunk = reader(_CHUNK)
                    except (socket.timeout, TimeoutError) as e:
                        raise _Retryable(f"read timeout {read_timeout:.0f}s на {downloaded}B") from e
                    except (ConnectionError, OSError, ValueError, AttributeError) as e:
                        # сокет разорван watchdog'ом (shutdown) или оборван сервером
                        if stalled["flag"]:
                            raise _Retryable(f"сталл >{stall_timeout:.0f}s на {downloaded}B") from e
                        raise _Retryable(f"обрыв на {downloaded}B: {e}") from e
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    seen[0] = downloaded
                    last[0] = time.time()
                    if progress_cb:
                        try:
                            progress_cb(downloaded, total)
                        except Exception:
                            pass
                    done_mb = downloaded // (1024 * 1024)
                    if total and done_mb >= next_mb:
                        pct = downloaded * 100 // total
                        _log(f"progress {done_mb}/{total // (1024 * 1024)} MB ({pct}%)")
                        next_mb = done_mb + step
        finally:
            stop.set()

        if stalled["flag"]:
            raise _Retryable(f"сталл >{stall_timeout:.0f}s на {downloaded}B")
        return downloaded, total


def download_model(
    spec: ModelSpec,
    progress_cb: Optional[ProgressCB] = None,
    cancel_event: Optional[threading.Event] = None,
    read_timeout: Optional[float] = None,
    stall_timeout: Optional[float] = None,
    max_retries: Optional[int] = None,
) -> str:
    """
    Скачать модель в local_model_path(spec) — устойчиво к зависаниям:
      • socket-таймаут на КАЖДУЮ операцию чтения (не только connect);
      • watchdog: если прогресс не растёт дольше stall_timeout — рвём и ретраим
        (ловит «trickle-сталл», который socket-таймаут не отлавливает);
      • докачка через HTTP Range, в т.ч. МЕЖДУ запусками приложения (остался .part
        → продолжаем с него, а не качаем 1.8 ГБ заново);
      • ретраи с backoff; подробный лог этапов в stderr.

    Ошибки → DownloadError. Отмена (cancel_event) → DownloadError, но .part
    сохраняется для последующей докачки. Параметры *_timeout/*_retries можно
    переопределить (используется в тестах для быстрого воспроизведения сталла).
    """
    read_timeout = _HTTP_TIMEOUT if read_timeout is None else read_timeout
    stall_timeout = _STALL_TIMEOUT if stall_timeout is None else stall_timeout
    max_retries = _MAX_RETRIES if max_retries is None else max_retries

    config.ensure_app_dirs()
    target = local_model_path(spec)
    part = target + ".part"

    if is_downloaded(spec):
        _log(f"{spec.id}: уже скачано → {target}")
        return target

    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
    except OSError as e:
        raise DownloadError(f"Не удалось создать папку для модели: {e}") from e

    url = _resolve_url(spec)
    ctx = _ssl_context()
    total_hint = spec.size_mb * 1024 * 1024 if spec.size_mb > 0 else 0

    # докачка между запусками: если остался .part — продолжаем с него
    downloaded = 0
    if os.path.exists(part):
        try:
            downloaded = os.path.getsize(part)
        except OSError:
            downloaded = 0
        if total_hint and downloaded > total_hint:
            _log(f".part больше ожидаемого ({downloaded}>{total_hint}) — начинаю заново")
            _cleanup(part)
            downloaded = 0
        elif downloaded > 0:
            _log(f"найден незавершённый .part ({downloaded}B) — докачка")

    _log(f"START {spec.id}: url={url} resume_from={downloaded}B")

    total = total_hint
    attempt = 0
    while True:
        attempt += 1
        try:
            downloaded, total = _download_attempt(
                url, part, ctx, downloaded, total or total_hint,
                read_timeout, stall_timeout, progress_cb, cancel_event, attempt,
            )
            if total and downloaded < total:
                raise _Retryable(f"неполно {downloaded}/{total}")
            break
        except _Cancelled:
            _log("отменено пользователем — .part сохранён для докачки")
            raise
        except DownloadError:
            _cleanup(part)
            raise
        except _Retryable as e:
            _log(f"attempt {attempt} прервана: {e}")
            # актуализируем прогресс по факту записанного .part (для докачки)
            try:
                if os.path.exists(part):
                    downloaded = os.path.getsize(part)
            except OSError:
                pass
            if attempt > max_retries:
                _cleanup(part)
                raise DownloadError(
                    f"Скачивание прервалось после {attempt} попыток ({e}). "
                    f"Проверьте интернет и запустите снова — докачка продолжится."
                )
            backoff = min(30.0, _RETRY_BACKOFF * (2 ** (attempt - 1)))
            _log(f"retry #{attempt} через {backoff:.0f}s (докачка c {downloaded}B)")
            _sleep_cancelable(backoff, cancel_event)
            continue

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

    _log(f"DONE {spec.id}: {got} bytes → {target}")
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
