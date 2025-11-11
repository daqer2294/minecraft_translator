# src/gui_main.py
from __future__ import annotations

# --- позволяем запускать как скрипт (а не только как модуль) ---
import os, sys
if __name__ == "__main__" and (__package__ is None or __package__ == ""):
    THIS = os.path.abspath(__file__)
    ROOT = os.path.dirname(os.path.dirname(THIS))  # <project>/src -> <project>
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    __package__ = "src"
# ----------------------------------------------------------------

import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from src.translators import Translator
from src.utils.cache import TranslationCache
from src import config
from src.mirrorer import mirror_translate_dir


def _base_dir_for_user_files() -> str:
    """
    Папка для пользовательских файлов (secrets.json, translations_cache.json):
    - если собрано PyInstaller'ом: рядом с исполняемым файлом
    - иначе: корень проекта
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Minecraft Translator — Mirror & Translate")
        self.geometry("780x560")

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar(value=os.path.abspath("./out_mirror"))
        self.dry_var = tk.BooleanVar(value=True)   # по умолчанию только проверка

        # ключ возьмётся из config.OPENAI_API_KEY; покажем статус
        self.key_ok = bool(config.OPENAI_API_KEY)

        self._build_ui()
        self._worker: threading.Thread | None = None

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        frm = ttk.Frame(self)
        frm.pack(fill="x", **pad)

        ttk.Label(frm, text="Входная папка (что переводить):").grid(row=0, column=0, sticky="w")
        e1 = ttk.Entry(frm, textvariable=self.input_var, width=74)
        e1.grid(row=1, column=0, sticky="we", **pad)
        ttk.Button(frm, text="Выбрать…", command=self.pick_input).grid(row=1, column=1, **pad)

        ttk.Label(frm, text="Папка вывода (куда положить результат):").grid(row=2, column=0, sticky="w")
        e2 = ttk.Entry(frm, textvariable=self.output_var, width=74)
        e2.grid(row=3, column=0, sticky="we", **pad)
        ttk.Button(frm, text="Выбрать…", command=self.pick_output).grid(row=3, column=1, **pad)

        # Режим
        ttk.Checkbutton(frm, text="Проверка без записи (dry-run)", variable=self.dry_var)\
            .grid(row=4, column=0, sticky="w", **pad)

        # Ключ
        key_status = "✅ Ключ найден (secrets.json/ENV)" if self.key_ok else "⚠️ Ключ не найден"
        self.key_lbl = ttk.Label(frm, text=key_status)
        self.key_lbl.grid(row=5, column=0, sticky="w", **pad)
        ttk.Button(frm, text="Задать ключ…", command=self.set_key).grid(row=5, column=1, **pad)

        # Кнопки управления
        btns = ttk.Frame(self)
        btns.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btns, text="Старт", command=self.start)
        self.start_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Открыть папку вывода", command=self.open_out).pack(side="left", padx=6)

        # Лог
        self.txt = tk.Text(self, height=20)
        self.txt.pack(fill="both", expand=True, **pad)
        self.log("Готово. Выберите входную папку (например, MC Eternal 2.1.1).")

    def pick_input(self):
        d = filedialog.askdirectory(title="Выбери входную папку")
        if d:
            self.input_var.set(d)

    def pick_output(self):
        d = filedialog.askdirectory(title="Выбери папку вывода (или создастся новая)")
        if d:
            self.output_var.set(d)

    def set_key(self):
        # сохраняем secrets.json в переносимое место (рядом с exe/app или в корне проекта)
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
            import json
            with open(secrets_path, "w", encoding="utf-8") as f:
                json.dump({"OPENAI_API_KEY": key}, f, ensure_ascii=False, indent=2)
            self.key_ok = True
            self.key_lbl.config(text="✅ Ключ сохранён (secrets.json)")
            self.log(f"🔑 Ключ сохранён: {secrets_path}")
            win.destroy()

        ttk.Button(win, text="Сохранить", command=save).pack(pady=10)

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

    def start(self):
        # не даём запускать несколько раз подряд
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

        self.log(f"▶️ Старт: {inp} → {out} | mode: {'dry-run' if self.dry_var.get() else 'write'}")

        # Блокируем кнопку на время работы
        self.start_btn.state(["disabled"])

        # write = not dry
        self._worker = threading.Thread(
            target=self._run_job,
            args=(inp, out, not self.dry_var.get()),
            daemon=True
        )
        self._worker.start()

    def _run_job(self, inp: str, out: str, write: bool):
        cache = TranslationCache(config.DEFAULT_CACHE_PATH)
        tr = Translator(config.TRANSLATOR_PROVIDER, config.TRANSLATOR_MODEL, config.OPENAI_API_KEY, cache, strict=True)

        def _logger(s: str):
            self.after(0, self.log, s)

        try:
            # mirror_translate_dir теперь возвращает (total, translated)
            total, translated = mirror_translate_dir(inp, out, tr, log=_logger, write=write)
            msg = f"✅ Готово. Результат: {out} ({'dry-run' if not write else 'saved'}) | files matched: {total}, translated: {translated}"
            self.after(0, self.log, msg)
            self.after(0, lambda: messagebox.showinfo("Готово", msg))
            self.after(0, self.open_out)
        except Exception as e:
            self.after(0, self.log, f"❌ Ошибка: {e}")
        finally:
            # разблокируем кнопку
            self.after(0, lambda: self.start_btn.state(["!disabled"]))


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
