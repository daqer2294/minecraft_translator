# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-спека — macOS (.app bundle, без code signing).
#
# Собирает onefile-бинарь и оборачивает его в MinecraftTranslator.app (BUNDLE),
# бундля статические веб-ассеты (src/gui/web/*). Backend WKWebView (cocoa)
# подтягивается pywebview автоматически при наличии pyobjc.
#
# Сборка:  pyinstaller --noconfirm --clean MinecraftTranslator-mac.spec
# Точка входа: src/main.py
#
# Примечание про архитектуру: target_arch=None → сборка под архитектуру раннера
# (arm64 на macos-14, x86_64 на macos-13). Универсальный бинарь (universal2) не
# делаем — зависит от universal2-колёс всех зависимостей (в т.ч. llama-cpp-python).

import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = [("src/gui/web", "src/gui/web")]
binaries = []
hiddenimports = [
    "webview",
    "webview.platforms.cocoa",  # WKWebView
    "objc",
    "Foundation",
    "WebKit",
    "Quartz",
]

# Если установлен llama-cpp-python (in-process инференс с Metal) — тащим его
# бинарники/данные (включая metal-шейдеры ggml).
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
    excludes=["tkinter"],
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
    upx=False,              # UPX на macOS ломает подписи/бинарники — выключено
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,       # арх раннера (arm64 / x86_64)
    codesign_identity=None, # без подписи
    entitlements_file=None,
)

app = BUNDLE(
    exe,
    name="MinecraftTranslator.app",
    icon=None,
    bundle_identifier="com.minecrafttranslator.app",
    info_plist={
        "CFBundleName": "Minecraft Translator",
        "CFBundleDisplayName": "Minecraft Translator",
        "CFBundleShortVersionString": "2.0.0",
        "CFBundleVersion": "2.0.0",
        "NSHighResolutionCapable": True,
        # разрешаем системную тёмную/светлую тему (prefers-color-scheme в UI)
        "NSRequiresAquaSystemAppearance": False,
        "LSMinimumSystemVersion": "11.0",
    },
)
