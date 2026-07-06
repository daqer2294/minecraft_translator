# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-спека — Windows (onefile .exe, без code signing).
#
# Собирает единый .exe и БУНДЛИТ статические веб-ассеты (src/gui/web/*), чтобы
# окно pywebview грузило index.html из exe. Backend WebView2 (EdgeChromium)
# подтягивается pywebview автоматически при наличии pythonnet.
#
# Сборка:  pyinstaller --noconfirm --clean MinecraftTranslator-win.spec
# Точка входа: src/main.py

import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = [("src/gui/web", "src/gui/web")]
binaries = []
hiddenimports = [
    "webview",
    "webview.platforms.edgechromium",  # WebView2
    "webview.platforms.winforms",      # запасной
    "clr",
    "clr_loader",
    "pythonnet",
    "certifi",                         # CA-бандл для HTTPS-скачивания моделей (BUG 1)
]

# Если установлен llama-cpp-python (in-process инференс) — тащим его бинарники/данные.
try:
    _d, _b, _h = collect_all("llama_cpp")
    datas += _d
    binaries += _b
    hiddenimports += _h
except Exception:
    pass

a = Analysis(
    ["src/main.py"],
    pathex=[os.path.abspath(".")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],  # legacy Tk GUI в exe не нужен
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MinecraftTranslator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # оконное приложение
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
