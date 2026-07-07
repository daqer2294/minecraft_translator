# src/gui/api.py
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from typing import Any, Dict, List, Optional

# --- бэкенд (НЕ модифицируется; вызывается через модульные атрибуты, чтобы
#     тесты могли их мокать: mirrorer.mirror_translate_dir и т.п.) ---
from .. import config
from ..utils.cache import TranslationCache
from ..translators import Translator
from .. import mirrorer
from ..llm import factory
from ..llm import hardware_probe as hp
from ..llm import model_downloader as dl
from ..llm import model_registry as registry
from ..llm import ollama_probe

LOG_TAIL = 400  # сколько последних строк лога держим для UI


class _StopRequested(Exception):
    """Кооперативная остановка перевода (бросается в on_tick на границе файла)."""


class _Control:
    """
    Кооперативные пауза/стоп. Backend (mirrorer) не знает про них — мы дёргаем
    checkpoint() из колбэков log/on_tick, которые mirrorer вызывает на границах
    файлов. Поэтому пауза/стоп срабатывают не мгновенно, а на ближайшей границе.
    """

    def __init__(self):
        self._cv = threading.Condition()
        self.paused = False
        self.stopped = False

    def reset(self):
        with self._cv:
            self.paused = False
            self.stopped = False

    def pause(self):
        with self._cv:
            self.paused = True

    def resume(self):
        with self._cv:
            self.paused = False
            self._cv.notify_all()

    def stop(self):
        with self._cv:
            self.stopped = True
            self.paused = False
            self._cv.notify_all()

    def checkpoint(self):
        with self._cv:
            while self.paused and not self.stopped:
                self._cv.wait()
            if self.stopped:
                raise _StopRequested()


def _base_dir_for_user_files() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # src/gui/api.py → корень проекта на два уровня выше src/
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class Api:
    """
    Мост pywebview ↔ Python. Экземпляр прокидывается в JS как
    window.pywebview.api. Все методы JSON-сериализуемы (возвращают dict/списки).

    Долгие операции (перевод, скачивание) запускаются в отдельных потоках,
    а UI забирает прогресс через polling get_status() (стабильнее, чем
    evaluate_js из фоновых потоков в pywebview).
    """

    def __init__(self):
        self.window = None  # выставляется в main.py после create_window
        self._lock = threading.RLock()
        self._logs: List[str] = []
        self._control = _Control()
        self._worker: Optional[threading.Thread] = None
        self._dl_thread: Optional[threading.Thread] = None
        self._dl_cancel: Optional[threading.Event] = None  # отмена активной загрузки
        self._start_time = 0.0

        hw = hp.load_cached()
        # Активный тир при старте — ВСЕГДА текущий PROVIDER.tier (по умолчанию
        # "light"). Рекомендацию из hardware.json (hw.recommended_tier) НЕ
        # применяем автоматически — только показываем в state.hardware. Иначе на
        # мощном железе Старт неожиданно требует скачать standard-модель (7B),
        # хотя пользователь уже работает на light (3B). Переключение тира —
        # только явным действием (set_tier / кнопка рекомендации).
        default_tier = getattr(config.PROVIDER, "tier", None) or "light"
        if default_tier not in ("light", "standard"):
            default_tier = "light"
        config.PROVIDER.tier = default_tier

        self._state: Dict[str, Any] = {
            "running": False,
            "paused": False,
            "phase": "idle",          # idle|translating|downloading|done|error|stopped
            "message": "",
            "input": "",
            "output": os.path.abspath("./out_mirror"),
            "dry": True,
            "lang": getattr(config, "TARGET_LANG", "ru_ru"),
            "mode": getattr(config.PROVIDER, "mode", "local"),
            "tier": default_tier,
            "key_present": bool(config.OPENAI_API_KEY),
            # прогресс перевода
            "total": 0, "done": 0, "ok": 0, "err": 0, "skip": 0,
            "speed": 0.0, "eta": 0.0,
            # прогресс скачивания
            "download": {"active": False, "cancelled": False, "name": "", "downloaded": 0, "total": 0},
            "hardware": self._hw_dict(hw),
            "model": None,
            # автодетект Ollama (заполняется detect_ollama по требованию)
            "ollama": {
                "checked": False, "available": False, "models": [],
                "model": getattr(config.PROVIDER, "ollama_model", ""),
                "base_url": getattr(config.PROVIDER, "ollama_base_url", ""),
                "recommended_pull": ollama_probe.recommended_pull_cmd(),
                "error": "",
            },
        }
        self._refresh_model_status()
        self._log("Готово. Выберите папку модпака и нажмите «Старт».")

    # ================= helpers =================

    def _hw_dict(self, hw) -> Optional[Dict[str, Any]]:
        if hw is None:
            return None
        return {
            "gpu_available": hw.gpu_available,
            "gpu_name": hw.gpu_name,
            "gpu_vram_mb": hw.gpu_vram_mb,
            "ram_mb": hw.ram_mb,
            "cpu_cores": hw.cpu_cores,
            "recommended_tier": hw.recommended_tier,
            "summary": hw.summary(),
        }

    def _log(self, msg: str):
        with self._lock:
            self._logs.append(str(msg))
            if len(self._logs) > LOG_TAIL:
                self._logs = self._logs[-LOG_TAIL:]

    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _snapshot(self) -> Dict[str, Any]:
        with self._lock:
            snap = dict(self._state)
            snap["download"] = dict(self._state["download"])
            snap["logs"] = list(self._logs)
            snap["can_pause"] = self._state["running"] and not self._state["paused"]
            snap["can_resume"] = self._state["running"] and self._state["paused"]
            return snap

    def _light_spec(self):
        mid = config.PROVIDER.light_model_id
        return registry.get_by_id(mid) if mid else registry.default_for_tier("light")

    def _standard_spec(self):
        mid = config.PROVIDER.standard_model_id
        return registry.get_by_id(mid) if mid else registry.default_for_tier("standard")

    def _spec_status(self, spec) -> Optional[Dict[str, Any]]:
        if spec is None:
            return None
        try:
            downloaded = bool(dl.is_downloaded(spec))
        except Exception:
            downloaded = False
        return {"id": spec.id, "label": spec.display, "size_mb": spec.size_mb, "downloaded": downloaded}

    def _refresh_model_status(self):
        light = self._spec_status(self._light_spec())
        standard = self._spec_status(self._standard_spec()) if config.PROVIDER.tier == "standard" else None
        self._set(model={"light": light, "standard": standard})

    # ================= простые сеттеры =================

    def get_init(self) -> Dict[str, Any]:
        """Начальные опции + текущее состояние (вызывается один раз при загрузке)."""
        langs = []
        mapping = getattr(config, "MC_LANG_NAMES", {}) or {"ru_ru": "Russian"}
        for code, name in mapping.items():
            langs.append({"code": code, "name": name})

        def _spec_opt(s):
            return {"id": s.id, "label": s.display, "size_mb": s.size_mb, "tier": s.tier}

        light_opts = [_spec_opt(s) for s in registry.list_by_tier("light")]
        std_opts = [_spec_opt(s) for s in (registry.list_by_tier("standard") + registry.list_by_tier("complex"))]

        return {
            "options": {
                "langs": langs,
                "modes": ["local", "external", "hybrid", "ollama"],
                "tiers": ["light", "standard"],
                "light_models": light_opts,
                "standard_models": std_opts,
                "light_model_id": config.PROVIDER.light_model_id or (self._light_spec().id if self._light_spec() else ""),
                "standard_model_id": config.PROVIDER.standard_model_id or (self._standard_spec().id if self._standard_spec() else ""),
            },
            "state": self._snapshot(),
        }

    def get_status(self) -> Dict[str, Any]:
        return self._snapshot()

    def set_input(self, path: str) -> Dict[str, Any]:
        self._set(input=(path or "").strip())
        return self._snapshot()

    def set_output(self, path: str) -> Dict[str, Any]:
        self._set(output=(path or "").strip() or os.path.abspath("./out_mirror"))
        return self._snapshot()

    def set_dry(self, value: bool) -> Dict[str, Any]:
        self._set(dry=bool(value))
        return self._snapshot()

    def set_lang(self, code: str) -> Dict[str, Any]:
        code = (code or "ru_ru").strip()
        config.TARGET_LANG = code
        self._set(lang=code)
        self._log(f"🌐 Язык перевода: {code}")
        return self._snapshot()

    def set_mode(self, mode: str) -> Dict[str, Any]:
        mode = (mode or "local").strip()
        if mode not in ("local", "external", "hybrid", "ollama"):
            mode = "local"
        config.PROVIDER.mode = mode
        self._set(mode=mode)
        self._log(f"⚙️ Режим провайдера: {mode}")
        return self._snapshot()

    # ================= Ollama (автодетект локального инференса) =================

    def detect_ollama(self) -> Dict[str, Any]:
        """
        Проверить локальную Ollama и её модели. Некритичный путь: любые ошибки
        превращаются в available=False, без исключений наружу.
        """
        base = getattr(config.PROVIDER, "ollama_base_url", ollama_probe.OLLAMA_DEFAULT_URL)
        st = ollama_probe.probe_ollama(base)
        models = [{"name": m.name, "size_mb": (m.size_bytes // (1024 * 1024))} for m in st.models]

        # авто-выбор разумной модели, если пользователь ещё не выбирал
        if st.available and st.models and not config.PROVIDER.ollama_model:
            sug = ollama_probe.suggest_model(st.models)
            if sug:
                config.PROVIDER.ollama_model = sug

        with self._lock:
            self._state["ollama"] = {
                "checked": True,
                "available": st.available,
                "models": models,
                "model": config.PROVIDER.ollama_model,
                "base_url": st.base_url,
                "recommended_pull": ollama_probe.recommended_pull_cmd(),
                "error": st.error,
            }
        if st.available:
            self._log(f"🦙 Ollama найдена ({st.base_url}), моделей: {len(models)}")
        else:
            self._log("🦙 Ollama не найдена (localhost:11434)")
        return self._snapshot()

    def set_ollama_model(self, model_name: str) -> Dict[str, Any]:
        """Выбрать модель Ollama и переключиться в режим ollama."""
        name = (model_name or "").strip()
        config.PROVIDER.ollama_model = name
        config.PROVIDER.mode = "ollama"
        with self._lock:
            self._state["mode"] = "ollama"
            self._state["ollama"]["model"] = name
        self._log(f"🦙 Модель Ollama: {name or '—'}")
        return self._snapshot()

    def open_url(self, url: str) -> Dict[str, Any]:
        """Открыть внешнюю ссылку в системном браузере (напр. ollama.com)."""
        try:
            import webbrowser
            webbrowser.open((url or "").strip())
        except Exception as e:
            self._set(message=f"Не удалось открыть ссылку: {e}")
        return self._snapshot()

    def set_tier(self, tier: str) -> Dict[str, Any]:
        tier = tier if tier in ("light", "standard") else "light"
        config.PROVIDER.tier = tier
        self._set(tier=tier)
        self._refresh_model_status()
        self._log(f"🎚 Тир модели: {tier}")
        return self._snapshot()

    def set_models(self, light_id: str = "", standard_id: str = "") -> Dict[str, Any]:
        if light_id and registry.get_by_id(light_id):
            config.PROVIDER.light_model_id = light_id
        if standard_id and registry.get_by_id(standard_id):
            config.PROVIDER.standard_model_id = standard_id
        self._refresh_model_status()
        self._log("🧩 Модели обновлены.")
        return self._snapshot()

    def set_key(self, key: str) -> Dict[str, Any]:
        key = (key or "").strip()
        if not key:
            self._set(message="Ключ пустой.")
            return self._snapshot()
        try:
            base_dir = _base_dir_for_user_files()
            os.makedirs(base_dir, exist_ok=True)
            with open(os.path.join(base_dir, "secrets.json"), "w", encoding="utf-8") as f:
                json.dump({"OPENAI_API_KEY": key}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._set(message=f"Не удалось сохранить ключ: {e}")
            return self._snapshot()
        config.OPENAI_API_KEY = key
        config.PROVIDER.external_api_key = key
        self._set(key_present=True, message="")
        self._log("🔑 API-ключ сохранён.")
        return self._snapshot()

    # ================= нативные диалоги =================

    def pick_folder(self, kind: str = "input") -> Dict[str, Any]:
        """Открыть нативный диалог выбора папки; сохранить в input/output."""
        path = ""
        try:
            import webview  # локальный импорт: недоступен в headless-тестах
            result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
            if result:
                path = result[0] if isinstance(result, (list, tuple)) else str(result)
        except Exception as e:
            self._set(message=f"Диалог выбора папки недоступен: {e}")
            return self._snapshot()
        if path:
            if kind == "output":
                self.set_output(path)
            else:
                self.set_input(path)
        return self._snapshot()

    # ================= железо =================

    def rescan_hardware(self) -> Dict[str, Any]:
        self._log("🔎 Проба железа…")

        def work():
            try:
                hw = hp.get_or_probe(force=True)
            except Exception as e:
                self._log(f"❌ Проба железа не удалась: {e}")
                return
            # BUG 1: НЕ меняем активный тир автоматически — только сохраняем пробу
            # и рекомендацию. Переключение тира — явным действием пользователя
            # (кнопка в UI → set_tier). Иначе Старт неожиданно требует скачать 7B.
            self._set(hardware=self._hw_dict(hw))
            self._refresh_model_status()
            rec = hw.recommended_tier
            cur = self._state.get("tier")
            if rec and rec != cur:
                self._log(f"🖥 {hw.summary()} — рекомендуется тир «{rec}» (текущий: «{cur}»). "
                          f"Переключить можно вручную.")
            else:
                self._log(f"🖥 {hw.summary()}")

        threading.Thread(target=work, daemon=True).start()
        return self._snapshot()

    # ================= модели =================

    def _needed_specs(self) -> List[Any]:
        specs = [self._light_spec()]
        if config.PROVIDER.tier == "standard":
            specs.append(self._standard_spec())
        return [s for s in specs if s]

    def _missing_specs(self) -> List[Any]:
        try:
            return [s for s in self._needed_specs() if not dl.is_downloaded(s)]
        except Exception:
            return self._needed_specs()

    def download_models(self, ids: Optional[List[str]] = None) -> Dict[str, Any]:
        if self._dl_thread and self._dl_thread.is_alive():
            self._set(message="Скачивание уже идёт…")
            return self._snapshot()

        if ids:
            specs = [registry.get_by_id(i) for i in ids]
            specs = [s for s in specs if s]
        else:
            specs = self._missing_specs()

        if not specs:
            self._log("Модели уже скачаны.")
            return self._snapshot()

        # BUG 2: событие отмены прокидываем в download_model (загрузчик его умеет)
        cancel = threading.Event()
        self._dl_cancel = cancel

        self._set(phase="downloading", message="")
        with self._lock:
            self._state["download"].update(
                {"active": True, "cancelled": False, "name": "", "downloaded": 0, "total": 0}
            )

        def work():
            failed = False
            cancelled = False
            try:
                for spec in specs:
                    if cancel.is_set():
                        cancelled = True
                        return
                    try:
                        url = dl._resolve_url(spec)
                    except Exception:
                        url = "(unresolved)"
                    self._log(f"⬇️ Скачивание {spec.display} (~{spec.size_mb} MB)…")
                    self._log(f"   URL: {url}")
                    with self._lock:
                        self._state["download"].update(
                            {"active": True, "name": spec.display, "downloaded": 0, "total": 0}
                        )

                    def cb(done, total, spec=spec):
                        # после отмены — колбэк ничего не пишет, чтобы active не
                        # «воскрешался» и модалка не переоткрывалась
                        if cancel.is_set():
                            return
                        with self._lock:
                            self._state["download"].update(
                                {"active": True, "name": spec.display,
                                 "downloaded": done, "total": total}
                            )

                    try:
                        dl.download_model(spec, progress_cb=cb, cancel_event=cancel)
                        self._log(f"✅ Скачано: {spec.display}")
                    except dl.DownloadError as e:
                        if cancel.is_set():
                            # штатная отмена пользователем — не ошибка
                            cancelled = True
                            self._log(f"⏹ Скачивание отменено: {spec.display}")
                            return
                        self._log(f"❌ Ошибка скачивания {spec.display}: {e}")
                        self._set(phase="error", message=str(e))
                        failed = True
                        return
                    except Exception as e:
                        traceback.print_exc()
                        self._log(f"❌ Непредвиденная ошибка при скачивании: {e!r}")
                        self._set(phase="error", message=str(e))
                        failed = True
                        return
                self._refresh_model_status()
                self._set(phase="idle")
                self._log("🎉 Модели готовы. Нажмите «Старт».")
            finally:
                with self._lock:
                    self._state["download"]["active"] = False
                    # cancelled-флаг держим, пока новый Старт/скачивание его не сбросит:
                    # updateDlModal по нему закрывает модалку и не переоткрывает
                    self._state["download"]["cancelled"] = cancelled
                if cancelled:
                    self._set(phase="idle", message="Скачивание отменено.")
                if failed or cancelled:
                    self._refresh_model_status()

        self._dl_thread = threading.Thread(target=work, daemon=True)
        self._dl_thread.start()
        return self._snapshot()

    def cancel_download(self) -> Dict[str, Any]:
        """Отменить активную загрузку модели (BUG 2)."""
        ev = self._dl_cancel
        if ev is not None and not ev.is_set():
            ev.set()
            self._log("⏹ Отмена скачивания…")
        # немедленно гасим активность + помечаем отменённым, чтобы модалка
        # закрылась на ближайшем polling-тике и не переоткрылась
        with self._lock:
            self._state["download"]["active"] = False
            self._state["download"]["cancelled"] = True
        return self._snapshot()

    # ================= перевод =================

    def _on_total(self, total: int):
        with self._lock:
            self._state.update({"total": total, "done": 0, "ok": 0, "err": 0, "skip": 0})
        self._start_time = time.time()

    def _on_tick(self, inc_done: int, inc_ok: int, inc_err: int, inc_skip: int):
        with self._lock:
            self._state["done"] += inc_done
            self._state["ok"] += inc_ok
            self._state["err"] += inc_err
            self._state["skip"] += inc_skip
            done = self._state["done"]
            total = self._state["total"] or 1
        elapsed = max(0.001, time.time() - self._start_time) if self._start_time else 0.001
        speed = done / elapsed
        remain = max(0, total - done)
        eta = remain / speed if speed > 0 else 0
        self._set(speed=round(speed, 2), eta=round(eta, 1), paused=self._control.paused)
        # кооперативные пауза/стоп на границе файла
        self._control.checkpoint()

    def _run_translation(self, inp: str, out: str, write: bool):
        try:
            cache = TranslationCache(config.DEFAULT_CACHE_PATH)
            cfg = config.PROVIDER
            cfg.mode = self._state["mode"]
            cfg.external_api_key = config.OPENAI_API_KEY
            cfg.tier = self._state["tier"]
            primary, complex_client = factory.build_clients(cfg)
            tr = Translator(primary, cache, strict=True, complex_client=complex_client, log=self._log)

            mirrorer.mirror_translate_dir(
                inp, out, tr,
                log=self._log,
                write=write,
                on_total=self._on_total,
                on_tick=self._on_tick,
            )
            self._set(phase="done", message="")
            self._log(f"✅ Готово. Результат: {out} ({'saved' if write else 'dry-run'})")
        except _StopRequested:
            self._set(phase="stopped")
            self._log("⏹ Остановлено пользователем.")
        except Exception as e:
            self._set(phase="error", message=str(e))
            self._log(f"❌ Ошибка: {e}")
        finally:
            self._set(running=False, paused=False)

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._state["running"]:
                self._state["message"] = "Процесс уже идёт…"
                return self._snapshot()
            # новый явный «Старт» сбрасывает флаг отмены (BUG 2): модалку снова
            # можно показать (need_download), если модель действительно нужна
            self._state["download"]["cancelled"] = False

        inp = (self._state["input"] or "").strip()
        out = (self._state["output"] or "").strip() or os.path.abspath("./out_mirror")
        if not inp:
            self._set(message="Укажите входную папку.")
            return self._snapshot()
        inp = os.path.abspath(os.path.expanduser(inp))
        out = os.path.abspath(os.path.expanduser(out))
        if not os.path.isdir(inp):
            self._set(message=f"Папка не найдена: {inp}")
            return self._snapshot()

        mode = self._state["mode"]

        # ollama: нужна выбранная модель (проверять/качать GGUF не надо)
        if mode == "ollama" and not config.PROVIDER.ollama_model:
            self._set(message="Выберите модель Ollama (режим «Локальная Ollama»).")
            return self._snapshot()

        # для локального/гибридного режима — нужна скачанная модель
        if mode in ("local", "hybrid") and not (
            config.PROVIDER.local_server_url or config.PROVIDER.local_model_path
        ):
            config.PROVIDER.tier = self._state["tier"]
            # первый запуск: если пробы железа ещё не было — прогоняем
            if hp.load_cached() is None:
                try:
                    hw = hp.get_or_probe(force=True)
                    self._set(hardware=self._hw_dict(hw))
                    self._log(f"🖥 {hw.summary()}")
                except Exception as e:
                    self._log(f"⚠️ Проба железа не удалась: {e}")
            missing = self._missing_specs()
            if missing:
                self._set(message="Требуется скачать модель.")
                snap = self._snapshot()
                # BUG 3: указываем тир каждой модели, чтобы UI мог пояснить
                # «нужна модель для тира X», а не путать с уже скачанной light.
                snap["need_download"] = [
                    {"id": s.id, "label": s.display, "size_mb": s.size_mb, "tier": s.tier}
                    for s in missing
                ]
                snap["active_tier"] = self._state["tier"]
                return snap

        # запускаем перевод
        self._control.reset()
        self._set(running=True, paused=False, phase="translating", message="",
                  total=0, done=0, ok=0, err=0, skip=0, speed=0.0, eta=0.0)
        self._log(
            f"▶️ Старт: {inp} → {out} | "
            f"{'dry-run' if self._state['dry'] else 'write'} | "
            f"lang: {self._state['lang']} | provider: {mode}"
        )
        write = not self._state["dry"]
        self._worker = threading.Thread(
            target=self._run_translation, args=(inp, out, write), daemon=True
        )
        self._worker.start()
        return self._snapshot()

    def pause(self) -> Dict[str, Any]:
        if self._state["running"]:
            self._control.pause()
            self._set(paused=True)
            self._log("⏸ Пауза (сработает на границе файла)…")
        return self._snapshot()

    def resume(self) -> Dict[str, Any]:
        if self._state["running"]:
            self._control.resume()
            self._set(paused=False)
            self._log("▶️ Продолжение…")
        return self._snapshot()

    def stop(self) -> Dict[str, Any]:
        if self._state["running"]:
            self._control.stop()
            self._log("⏹ Остановка (сработает на границе файла)…")
        return self._snapshot()
