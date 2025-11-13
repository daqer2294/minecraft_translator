# src/gui_main.py
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
        key_status = "✅ Ключ найден (secrets.json/ENV)" if self.key_ok else "⚠️ Ключ не найден"
        self.key_lbl = ttk.Label(frm, text=key_status)
        self.key_lbl.grid(row=6, column=0, sticky="w", **pad)
        ttk.Button(frm, text="Задать ключ…", command=self.set_key).grid(row=6, column=1, **pad)

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
            self.key_ok = True
            self.key_lbl.config(text="✅ Ключ сохранён (secrets.json)")
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

        self.log(f"▶️ Старт: {inp} → {out} | mode: {'dry-run' if self.dry_var.get() else 'write'} | lang: {self.lang_var.get()}")
        self._on_total(0)
        self._worker = threading.Thread(target=self._run_job, args=(inp, out, not self.dry_var.get()), daemon=True)
        self._worker.start()

    def _run_job(self, inp: str, out: str, write: bool):
        cache = TranslationCache(config.DEFAULT_CACHE_PATH)
        tr = Translator(
            config.TRANSLATOR_PROVIDER,
            config.TRANSLATOR_MODEL,
            config.OPENAI_API_KEY,
            cache,
            strict=True,
        )

        def _logger(s: str):
            self.after(0, self.log, s)

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
