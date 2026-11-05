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
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

# Версия в свойствах exe: без неё SmartScreen и антивирусы видят «пустой» файл.
_version_ns = {}
with open(os.path.join("src", "version.py"), encoding="utf-8") as _f:
    exec(_f.read(), _version_ns)
VERSION = _version_ns["__version__"]
_nums = tuple(int(x) for x in VERSION.split(".")) + (0,) * (4 - len(VERSION.split(".")))
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_nums, prodvers=_nums, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                      subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "daqer2294"),
            StringStruct("FileDescription", "Minecraft Translator"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "MinecraftTranslator"),
            StringStruct("OriginalFilename", "MinecraftTranslator.exe"),
            StringStruct("ProductName", "Minecraft Translator"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

block_cipher = None

datas = [("src/gui/web", "src/gui/web"), ("src/data", "src/data")]
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
    # UPX выключен: сжатый им exe чаще принимают за вирус.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # оконное приложение
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=version_info,
)
