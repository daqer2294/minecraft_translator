# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-спека — macOS (.app bundle, без code signing).
#
# ONEDIR-сборка (EXE → COLLECT → BUNDLE), а НЕ onefile.
# Почему onedir (BUG 2 — дублирование Spaces/Dock при запуске):
#   PyInstaller официально НЕ рекомендует onefile+windowed для macOS .app —
#   такой bundle распаковывается при каждом запуске и ломает активацию окна
#   (двойной Dock-иконка, повторный запуск, создание новых Spaces). onedir —
#   штатная раскладка .app, окно ведёт себя как обычное приложение.
#
# Бандлит статические веб-ассеты (src/gui/web/*). Backend WKWebView (cocoa)
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
    "certifi",                  # CA-бандл для HTTPS-скачивания моделей (BUG 1)
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

# ONEDIR: EXE без бинарников (exclude_binaries=True) → COLLECT → BUNDLE.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MinecraftTranslator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # UPX на macOS ломает подписи/бинарники — выключено
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,       # арх раннера (arm64 / x86_64)
    codesign_identity=None, # без подписи
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MinecraftTranslator",
)

app = BUNDLE(
    coll,
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
        # macOS TCC: тексты, которые система показывает в запросе доступа к
        # защищённым папкам. Без них не-sandbox приложение может тихо получать
        # отказ вместо понятного запроса при чтении Documents/Desktop/Downloads.
        "NSDocumentsFolderUsageDescription":
            "Доступ к папке нужен, чтобы прочитать файлы модпака для перевода "
            "(модпаки обычно лежат в Documents, напр. CurseForge/Instances).",
        "NSDesktopFolderUsageDescription":
            "Доступ к папке нужен, чтобы прочитать файлы модпака для перевода, "
            "если он лежит на Рабочем столе.",
        "NSDownloadsFolderUsageDescription":
            "Доступ к папке нужен, чтобы прочитать файлы модпака для перевода, "
            "если он лежит в Загрузках.",
        "NSRemovableVolumesUsageDescription":
            "Доступ нужен, чтобы прочитать файлы модпака с внешнего диска.",
    },
)
