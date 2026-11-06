# src/platform_guard.py
"""
Проверки окружения перед стартом окна. Нужны прежде всего оконному exe на Windows.
"""
from __future__ import annotations

import os
import platform
import sys
from typing import List

WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def ensure_std_streams() -> List[int]:
    """
    У оконного приложения Windows нет консоли: sys.stdout и sys.stderr равны None,
    а дескрипторы 0-2 не открыты (так описано в документации sys.__stdout__).
    llama_cpp при verbose=False делает os.dup(1) и упал бы на загрузке модели.
    Пустые потоки и дескрипторы направляются в os.devnull. Возвращает исправленные дескрипторы.
    """
    for name, mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, mode, encoding="utf-8"))
    fixed = []
    for fd in (0, 1, 2):
        try:
            os.fstat(fd)
        except OSError:
            null = os.open(os.devnull, os.O_RDWR)
            if null != fd:
                os.dup2(null, fd)
                os.close(null)
            fixed.append(fd)
    return fixed


def webview2_missing() -> bool:
    """
    Без WebView2 Runtime (или .NET 4.6.2) pywebview на Windows молча берёт старый движок
    MSHTML (Internet Explorer). Наш интерфейс на нём не работает: async/await, переменные CSS.
    Проверка та же, что у pywebview. При любой ошибке считаем, что всё в порядке.
    """
    if platform.system() != "Windows":
        return False
    try:
        from webview.platforms import winforms
        return not winforms.is_chromium
    except Exception:
        return False


def ask_to_install_webview2() -> None:
    """Окно Windows с объяснением. «Да» открывает страницу загрузки Microsoft."""
    import ctypes
    import webbrowser

    text = (
        "Не найден Microsoft Edge WebView2 Runtime (или .NET Framework 4.6.2 и новее).\n"
        "Без него окно приложения не работает.\n\n"
        "Открыть страницу загрузки WebView2?"
    )
    mb_yesno, mb_iconwarning, idyes = 0x04, 0x30, 6
    answer = ctypes.windll.user32.MessageBoxW(None, text, "Minecraft Translator", mb_yesno | mb_iconwarning)
    if answer == idyes:
        webbrowser.open(WEBVIEW2_URL)
