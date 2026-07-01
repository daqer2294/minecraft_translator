# src/main.py — точка входа pywebview-версии GUI.
from __future__ import annotations

import os
import sys

# --- запуск как скрипт (pyinstaller / python src/main.py) ---
if __name__ == "__main__" and (__package__ is None or __package__ == ""):
    THIS = os.path.abspath(__file__)
    ROOT = os.path.dirname(os.path.dirname(THIS))
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    __package__ = "src"
# ------------------------------------------------------------

from src.gui.api import Api


def _resource_dir() -> str:
    """
    Путь к папке со статикой web/. В PyInstaller-onefile файлы распаковываются
    в sys._MEIPASS; в обычном запуске — рядом с этим файлом.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS  # type: ignore[attr-defined]
        # в .spec данные кладём как src/gui/web
        candidate = os.path.join(base, "src", "gui", "web")
        if os.path.isdir(candidate):
            return candidate
        return os.path.join(base, "gui", "web")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui", "web")


def main():
    import webview  # импорт здесь, чтобы модуль импортировался и без pywebview

    index = os.path.join(_resource_dir(), "index.html")
    api = Api()
    window = webview.create_window(
        "Minecraft Translator",
        url=index,
        js_api=api,
        width=1024,
        height=760,
        min_size=(820, 620),
    )
    api.window = window

    # Выбор backend'а. По умолчанию pywebview определяет его сам:
    #   Windows → edgechromium (WebView2),  macOS → cocoa (WKWebView),
    #   Linux   → gtk/qt.
    # Явно форсировать gui= НЕ нужно и рискованно (при отсутствии бэкенда упадёт),
    # поэтому оставляем авто-режим, но даём возможность переопределить через env
    # MCT_GUI (напр. "edgechromium", "cocoa", "gtk", "qt") для отладки.
    gui = os.environ.get("MCT_GUI") or None
    start_kwargs = {"debug": bool(os.environ.get("MCT_DEBUG"))}
    if gui:
        start_kwargs["gui"] = gui
    webview.start(**start_kwargs)


if __name__ == "__main__":
    main()
