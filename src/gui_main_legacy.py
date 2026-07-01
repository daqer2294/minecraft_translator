# src/gui_main_legacy.py
# LEGACY Tkinter GUI — оставлен как fallback. Актуальная точка входа — src/main.py
# (pywebview). Запуск legacy: `python -m src.gui_main_legacy`.
from __future__ import annotations

# --- запуск как скрипт ---
import os, sys
if __name__ == "__main__" and (__package__ is None or __package__ == ""):
    THIS = os.path.abspath(__file__)
    ROOT = os.path.dirname(os.path.dirname(THIS))
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    __package__ = "src"
# -------------------------

import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json

from src.translators import Translator
from src.utils.cache import TranslationCache
from src import config
from src.llm import build_clients
from src.llm import hardware_probe as hp
from src.llm import model_downloader as dl
from src.llm import model_registry as registry
from src.mirrorer import mirror_translate_dir


def _base_dir_for_user_files() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Minecraft Translator — Mirror & Translate")
        self.geometry("840x620")

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar(value=os.path.abspath("./out_mirror"))
        self.dry_var = tk.BooleanVar(value=True)

        # язык перевода
        self.lang_var = tk.StringVar(value=getattr(config, "TARGET_LANG", "ru_ru"))

        # режим провайдера: local | external | hybrid
        self.mode_var = tk.StringVar(value=getattr(config.PROVIDER, "mode", "local"))

        # железо / тир локальной модели
        self._hw = hp.load_cached()
        default_tier = getattr(config.PROVIDER, "tier", "") or (
            self._hw.recommended_tier if self._hw else "light"
        )
        self.tier_var = tk.StringVar(value=default_tier)
        config.PROVIDER.tier = default_tier
        self._dl_thread: threading.Thread | None = None

        self.key_ok = bool(config.OPENAI_API_KEY)
        self._worker: threading.Thread | None = None

        # прогресс
        self.total_var = tk.IntVar(value=0)
        self.done_var = tk.IntVar(value=0)
        self.ok_var = tk.IntVar(value=0)
        self.err_var = tk.IntVar(value=0)
        self.skip_var = tk.IntVar(value=0)
        self.speed_var = tk.StringVar(value="—")
        self.eta_var = tk.StringVar(value="—")
        self._start_time = 0.0

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        frm = ttk.Frame(self)
        frm.pack(fill="x", **pad)

        ttk.Label(frm, text="Входная папка (что переводить и копировать):").grid(row=0, column=0, sticky="w")
        e1 = ttk.Entry(frm, textvariable=self.input_var, width=70)
        e1.grid(row=1, column=0, sticky="we", **pad)
        ttk.Button(frm, text="Выбрать…", command=self.pick_input).grid(row=1, column=1, **pad)

        ttk.Label(frm, text="Папка вывода (куда положить результат):").grid(row=2, column=0, sticky="w")
        e2 = ttk.Entry(frm, textvariable=self.output_var, width=70)
        e2.grid(row=3, column=0, sticky="we", **pad)
        ttk.Button(frm, text="Выбрать…", command=self.pick_output).grid(row=3, column=1, **pad)

        # режим
        ttk.Checkbutton(frm, text="Проверка без записи (dry-run)", variable=self.dry_var).grid(row=4, column=0, sticky="w", **pad)

        # выбор режима провайдера (local / external / hybrid)
        mode_frame = ttk.Frame(frm)
        mode_frame.grid(row=4, column=1, sticky="w", **pad)
        ttk.Label(mode_frame, text="Режим:").pack(side="left")
        self.mode_combo = ttk.Combobox(
            mode_frame,
            state="readonly",
            values=["local", "external", "hybrid"],
            width=12,
        )
        self.mode_combo.set(self.mode_var.get())
        self.mode_combo.bind("<<ComboboxSelected>>", self.on_mode_changed)
        self.mode_combo.pack(side="left", padx=6)

        # выбор языка
        lang_frame = ttk.Frame(frm)
        lang_frame.grid(row=5, column=0, sticky="w", **pad)

        ttk.Label(lang_frame, text="Язык перевода (Minecraft locale):").pack(side="left")

        # список языков из config.MC_LANG_NAMES, если есть
        lang_codes = []
        lang_display = []
        mapping = getattr(config, "MC_LANG_NAMES", None)
        if isinstance(mapping, dict) and mapping:
            for code, name in mapping.items():
                lang_codes.append(code)
                lang_display.append(f"{name} ({code})")
        else:
            lang_codes = [self.lang_var.get()]
            lang_display = [self.lang_var.get()]

        self.lang_combo = ttk.Combobox(
            lang_frame,
            state="readonly",
            values=lang_display,
            width=30,
        )
        # установить текущий
        try:
            idx = [c for c in lang_codes].index(self.lang_var.get())
        except ValueError:
            idx = 0
        self._lang_codes = lang_codes
        self.lang_combo.current(idx)
        self.lang_combo.bind("<<ComboboxSelected>>", self.on_lang_changed)

        self.lang_combo.pack(side="left", padx=6)

        # Ключ
        self.key_lbl = ttk.Label(frm, text="")
        self.key_lbl.grid(row=6, column=0, sticky="w", **pad)
        self._refresh_key_label()
        ttk.Button(frm, text="Задать ключ…", command=self.set_key).grid(row=6, column=1, **pad)

        # Локальная модель / железо
        hw_frame = ttk.LabelFrame(self, text="Локальная модель / железо")
        hw_frame.pack(fill="x", **pad)
        row1 = ttk.Frame(hw_frame)
        row1.pack(fill="x", padx=4, pady=2)
        ttk.Label(row1, text="Тир:").pack(side="left", padx=4)
        self.tier_combo = ttk.Combobox(
            row1, state="readonly", values=["light", "standard"], width=10
        )
        self.tier_combo.set(self.tier_var.get())
        self.tier_combo.bind("<<ComboboxSelected>>", self.on_tier_changed)
        self.tier_combo.pack(side="left", padx=4)
        ttk.Button(row1, text="Пересканировать железо", command=self.on_rescan_hardware).pack(side="left", padx=4)
        ttk.Button(row1, text="Сменить модель…", command=self.on_change_model).pack(side="left", padx=4)
        self.hw_lbl = ttk.Label(hw_frame, text="", justify="left")
        self.hw_lbl.pack(anchor="w", padx=6, pady=2)
        self._refresh_hw_label()

        # Панель управления
        btns = ttk.Frame(self)
        btns.pack(fill="x", **pad)
        ttk.Button(btns, text="Старт", command=self.start).pack(side="left", padx=6)
        ttk.Button(btns, text="Открыть папку вывода", command=self.open_out).pack(side="left", padx=6)

        # Прогрессбар + статус
        prog = ttk.Frame(self)
        prog.pack(fill="x", **pad)
        ttk.Label(prog, text="Прогресс:").pack(anchor="w")
        self.pbar = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.pbar.pack(fill="x", padx=4, pady=4)

        self.prog_lbl = ttk.Label(
            prog,
            text="0/0 • ок:0 • skip:0 • err:0 • скорость: — • ETA: —"
        )
        self.prog_lbl.pack(anchor="w")

        # Лог
        self.txt = tk.Text(self, height=18)
        self.txt.pack(fill="both", expand=True, **pad)
        self.log("Готово. Выберите входную папку (например, MC Eternal 2.1.1).")

    # ---------- выбор путей / языка ----------

    def pick_input(self):
        d = filedialog.askdirectory(title="Выбери входную папку")
        if d:
            self.input_var.set(d)

    def pick_output(self):
        d = filedialog.askdirectory(title="Выбери папку вывода (или создастся новая)")
        if d:
            self.output_var.set(d)

    def on_lang_changed(self, event=None):
        idx = self.lang_combo.current()
        if 0 <= idx < len(self._lang_codes):
            code = self._lang_codes[idx]
            self.lang_var.set(code)
            config.TARGET_LANG = code
            self.log(f"🌐 Язык перевода установлен: {code}")

    def on_mode_changed(self, event=None):
        mode = self.mode_combo.get()
        self.mode_var.set(mode)
        config.PROVIDER.mode = mode
        self._refresh_key_label()
        self.log(f"⚙️ Режим провайдера: {mode}")

    def _refresh_key_label(self):
        """
        Ключ обязателен только для external/hybrid. В local-режиме он не нужен —
        не пугаем пользователя предупреждением.
        """
        mode = self.mode_var.get()
        if mode == "local":
            self.key_lbl.config(text="💻 Локальный режим — API-ключ не требуется")
        elif self.key_ok:
            self.key_lbl.config(text="✅ Ключ найден (secrets.json/ENV)")
        else:
            self.key_lbl.config(text="⚠️ Нужен API-ключ для режима external/hybrid")

    # ---------- железо / локальная модель ----------

    def _current_light_spec(self):
        mid = config.PROVIDER.light_model_id
        return registry.get_by_id(mid) if mid else registry.default_for_tier("light")

    def _current_standard_spec(self):
        mid = config.PROVIDER.standard_model_id
        return registry.get_by_id(mid) if mid else registry.default_for_tier("standard")

    def _refresh_hw_label(self):
        tier = self.tier_var.get()
        spec = self._current_light_spec()
        try:
            state = "скачана" if (spec and dl.is_downloaded(spec)) else "не скачана"
        except Exception:
            state = "?"
        hw = self._hw.summary() if self._hw else "проба ещё не выполнялась"
        model_name = spec.display if spec else "—"
        self.hw_lbl.config(text=f"Железо: {hw}\nМодель ({tier}): {model_name} — {state}")

    def on_tier_changed(self, event=None):
        self.tier_var.set(self.tier_combo.get())
        config.PROVIDER.tier = self.tier_var.get()
        self._refresh_hw_label()
        self.log(f"🎚 Тир модели: {self.tier_var.get()}")

    def on_rescan_hardware(self):
        self.log("🔎 Проба железа…")

        def work():
            try:
                hw = hp.get_or_probe(force=True)
            except Exception as e:
                self.after(0, self.log, f"❌ Проба железа не удалась: {e}")
                return

            def apply():
                self._hw = hw
                self.tier_var.set(hw.recommended_tier)
                self.tier_combo.set(hw.recommended_tier)
                config.PROVIDER.tier = hw.recommended_tier
                self._refresh_hw_label()
                self.log(f"🖥 {hw.summary()}")

            self.after(0, apply)

        threading.Thread(target=work, daemon=True).start()

    def on_change_model(self):
        win = tk.Toplevel(self)
        win.title("Выбор локальной модели")
        win.geometry("620x240")

        light_specs = registry.list_by_tier("light")
        std_specs = registry.list_by_tier("standard") + registry.list_by_tier("complex")

        ttk.Label(win, text="Лёгкая модель (простые строки):").pack(anchor="w", padx=10, pady=(10, 2))
        light_combo = ttk.Combobox(win, state="readonly", width=70, values=[s.display for s in light_specs])
        cur_l = self._current_light_spec()
        light_combo.current(light_specs.index(cur_l) if cur_l in light_specs else 0)
        light_combo.pack(fill="x", padx=10)

        ttk.Label(win, text="Мощная модель (сложные строки — лор/квесты):").pack(anchor="w", padx=10, pady=(10, 2))
        std_combo = ttk.Combobox(win, state="readonly", width=70, values=[s.display for s in std_specs])
        cur_s = self._current_standard_spec()
        std_combo.current(std_specs.index(cur_s) if cur_s in std_specs else 0)
        std_combo.pack(fill="x", padx=10)

        def save():
            li, si = light_combo.current(), std_combo.current()
            if 0 <= li < len(light_specs):
                config.PROVIDER.light_model_id = light_specs[li].id
            if 0 <= si < len(std_specs):
                config.PROVIDER.standard_model_id = std_specs[si].id
            self._refresh_hw_label()
            self.log("🧩 Модели обновлены.")
            win.destroy()

        ttk.Button(win, text="Сохранить", command=save).pack(pady=12)

    def _ensure_models_ready(self) -> bool:
        """
        Гарантировать наличие локальной модели перед стартом (local/hybrid).
        Возвращает True — можно стартовать перевод; False — запущено скачивание
        или пользователь отказался (перевод не стартуем сейчас).
        """
        # server mode / явный путь → модель не качаем сами
        if config.PROVIDER.local_server_url or config.PROVIDER.local_model_path:
            return True

        config.PROVIDER.tier = self.tier_var.get()

        # первый запуск: пробы железа ещё не было
        if hp.load_cached() is None:
            try:
                hw = hp.get_or_probe(force=True)
                self._hw = hw
                use = messagebox.askyesno(
                    "Проба железа",
                    f"{hw.summary()}\n\nИспользовать рекомендованный тир "
                    f"'{hw.recommended_tier}'?\n(Нет — оставить '{self.tier_var.get()}')",
                )
                if use:
                    self.tier_var.set(hw.recommended_tier)
                    self.tier_combo.set(hw.recommended_tier)
                    config.PROVIDER.tier = hw.recommended_tier
                self._refresh_hw_label()
            except Exception as e:
                self.log(f"⚠️ Проба железа не удалась: {e}")

        # какие модели нужны для выбранного тира
        specs = [self._current_light_spec()]
        if config.PROVIDER.tier == "standard":
            specs.append(self._current_standard_spec())
        specs = [s for s in specs if s]

        try:
            missing = [s for s in specs if not dl.is_downloaded(s)]
        except Exception as e:
            self.log(f"⚠️ Не удалось проверить модели: {e}")
            missing = specs

        if not missing:
            return True

        total_mb = sum(s.size_mb for s in missing)
        names = ", ".join(s.display for s in missing)
        if messagebox.askyesno(
            "Скачивание модели",
            f"Требуется скачать:\n{names}\n\nПримерно {total_mb} MB. Скачать сейчас?",
        ):
            self._start_download(missing)
        else:
            self.log("⏸ Скачивание отменено — локальный перевод недоступен без модели.")
        return False

    def _start_download(self, specs):
        if self._dl_thread and self._dl_thread.is_alive():
            messagebox.showinfo("Занято", "Скачивание уже идёт…")
            return
        self._on_total(0)

        def work():
            for spec in specs:
                self.after(0, self.log, f"⬇️ Скачивание {spec.display} (~{spec.size_mb} MB)…")

                def prog(done, total, spec=spec):
                    def apply():
                        self.total_var.set(max(total, 1))
                        self.done_var.set(done)
                        self._update_progress_ui()
                    self.after(0, apply)

                try:
                    dl.download_model(spec, progress_cb=prog)
                    self.after(0, self.log, f"✅ Скачано: {spec.display}")
                except dl.DownloadError as e:
                    self.after(0, self.log, f"❌ Ошибка скачивания {spec.display}: {e}")
                    self.after(0, lambda e=e: messagebox.showerror("Ошибка скачивания", str(e)))
                    return
                except Exception as e:
                    self.after(0, self.log, f"❌ Непредвиденная ошибка при скачивании: {e}")
                    return
            self.after(0, self._refresh_hw_label)
            self.after(0, self.log, "🎉 Модели готовы. Нажмите «Старт» ещё раз для перевода.")
            self.after(0, lambda: messagebox.showinfo("Готово", "Модель(и) скачаны. Нажмите «Старт»."))

        self._dl_thread = threading.Thread(target=work, daemon=True)
        self._dl_thread.start()

    def set_key(self):
        base_dir = _base_dir_for_user_files()
        secrets_path = os.path.join(base_dir, "secrets.json")

        win = tk.Toplevel(self)
        win.title("OpenAI API Key")
        win.geometry("520x160")
        sv = tk.StringVar()
        ttk.Label(win, text="Вставь OpenAI API ключ:").pack(anchor="w", padx=10, pady=6)
        ent = ttk.Entry(win, textvariable=sv, width=60)
        ent.pack(fill="x", padx=10)
        ent.focus_set()

        def save():
            key = sv.get().strip()
            if not key:
                messagebox.showerror("Ошибка", "Ключ пустой.")
                return
            os.makedirs(base_dir, exist_ok=True)
            with open(secrets_path, "w", encoding="utf-8") as f:
                json.dump({"OPENAI_API_KEY": key}, f, ensure_ascii=False, indent=2)
            # применяем ключ в текущей сессии (иначе _run_job взял бы пустой)
            config.OPENAI_API_KEY = key
            config.PROVIDER.external_api_key = key
            self.key_ok = True
            self._refresh_key_label()
            self.log(f"🔑 Ключ сохранён: {secrets_path}")
            win.destroy()

        ttk.Button(win, text="Сохранить", command=save).pack(pady=10)

    # ---------- утилиты ----------

    def open_out(self):
        out_dir = self.output_var.get().strip() or "./out_mirror"
        out_dir = os.path.abspath(os.path.expanduser(out_dir))
        os.makedirs(out_dir, exist_ok=True)
        if os.name == "posix":
            os.system(f'open "{out_dir}"')
        else:
            try:
                os.startfile(out_dir)  # type: ignore[attr-defined]
            except Exception:
                messagebox.showinfo("Папка вывода", out_dir)

    def log(self, msg: str):
        self.txt.insert("end", msg + "\n")
        self.txt.see("end")
        self.update_idletasks()

    # ---------- прогресс ----------

    def _on_total(self, total: int):
        self.total_var.set(total)
        self.done_var.set(0)
        self.ok_var.set(0)
        self.err_var.set(0)
        self.skip_var.set(0)
        self._start_time = time.time()
        self._update_progress_ui()

    def _on_tick(self, inc_done: int, inc_ok: int, inc_err: int, inc_skip: int):
        def apply():
            self.done_var.set(self.done_var.get() + inc_done)
            self.ok_var.set(self.ok_var.get() + inc_ok)
            self.err_var.set(self.err_var.get() + inc_err)
            self.skip_var.set(self.skip_var.get() + inc_skip)
            self._update_progress_ui()
        self.after(0, apply)

    def _update_progress_ui(self):
        total = self.total_var.get() or 1
        done = self.done_var.get()
        ok = self.ok_var.get()
        err = self.err_var.get()
        skip = self.skip_var.get()

        self.pbar.configure(maximum=total, value=done)

        elapsed = max(0.001, time.time() - self._start_time) if self._start_time else 0.001
        speed = done / elapsed
        self.speed_var.set(f"{speed:.2f}/с")
        remain = max(0, total - done)
        eta = remain / speed if speed > 0 else 0
        self.eta_var.set(f"{int(eta)}с" if eta < 3600 else f"{eta/3600:.1f}ч")

        self.prog_lbl.config(
            text=f"{done}/{total} • ок:{ok} • skip:{skip} • err:{err} • "
                 f"скорость: {self.speed_var.get()} • ETA: {self.eta_var.get()}"
        )

    # ---------- запуск ----------

    def start(self):
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("Занято", "Процесс уже идёт…")
            return

        inp = self.input_var.get().strip()
        out = self.output_var.get().strip() or "./out_mirror"
        if not inp:
            messagebox.showerror("Ошибка", "Укажи входную папку.")
            return

        inp = os.path.abspath(os.path.expanduser(inp))
        out = os.path.abspath(os.path.expanduser(out))
        if not os.path.isdir(inp):
            messagebox.showerror("Ошибка", f"Папка не найдена:\n{inp}")
            return

        # для локального/гибридного режима убедимся, что модель скачана;
        # при необходимости запускаем проба-железа + скачивание (не блокируя UI)
        if self.mode_var.get() in ("local", "hybrid"):
            if not self._ensure_models_ready():
                return

        self.log(
            f"▶️ Старт: {inp} → {out} | "
            f"{'dry-run' if self.dry_var.get() else 'write'} | "
            f"lang: {self.lang_var.get()} | provider: {self.mode_var.get()}"
        )
        self._on_total(0)
        self._worker = threading.Thread(target=self._run_job, args=(inp, out, not self.dry_var.get()), daemon=True)
        self._worker.start()

    def _run_job(self, inp: str, out: str, write: bool):
        cache = TranslationCache(config.DEFAULT_CACHE_PATH)

        # Собираем клиент(ы) по текущему режиму провайдера.
        cfg = config.PROVIDER
        cfg.mode = self.mode_var.get()
        cfg.external_api_key = config.OPENAI_API_KEY  # подхватить ключ, если задан
        primary, complex_client = build_clients(cfg)

        def _logger(s: str):
            self.after(0, self.log, s)

        tr = Translator(
            primary,
            cache,
            strict=True,
            complex_client=complex_client,
            log=_logger,
        )

        try:
            mirror_translate_dir(
                inp,
                out,
                tr,
                log=_logger,
                write=write,
                on_total=lambda total: self.after(0, self._on_total, total),
                on_tick=lambda inc_done, inc_ok, inc_err, inc_skip: self._on_tick(inc_done, inc_ok, inc_err, inc_skip),
            )
            self.after(0, self.log, f"✅ Готово. Результат: {out} ({'dry-run' if not write else 'saved'})")
        except Exception as e:
            self.after(0, self.log, f"❌ Ошибка: {e}")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
