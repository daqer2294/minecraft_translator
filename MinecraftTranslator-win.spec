# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-спека — Windows (папка onedir с MinecraftTranslator.exe, без code signing).
#
# Папка, а не один exe: onefile при каждом запуске распаковывает себя во временную
# папку и грузит оттуда DLL. Это медленно и чаще вызывает ложные срабатывания антивирусов.
#
# Собирает exe и БУНДЛИТ статические веб-ассеты (src/gui/web/*), чтобы
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

# Сборка win-x64-cuda-b: CUDA-библиотеки из пакетов nvidia-cuda-runtime-cu12 и nvidia-cublas-cu12
# в _internal/nvidia/<пакет>/bin. Перед импортом llama_cpp src/win_dll.py добавляет эти папки
# в PATH и os.add_dll_directory. Без MCT_BUNDLE_CUDA=1 сборка как раньше.
CUDA_DLLS = {"cuda_runtime": ["cudart64_12.dll"], "cublas": ["cublas64_12.dll", "cublasLt64_12.dll"]}
if os.environ.get("MCT_BUNDLE_CUDA") == "1":
    import sys as _sys
    for _pkg, _names in CUDA_DLLS.items():
        for _name in _names:
            _found = [os.path.join(p, "nvidia", _pkg, "bin", _name) for p in _sys.path
                      if os.path.isfile(os.path.join(p, "nvidia", _pkg, "bin", _name))]
            if not _found:
                raise SystemExit(f"MCT_BUNDLE_CUDA=1, но нет nvidia/{_pkg}/bin/{_name}: поставьте requirements-win-cuda-b.txt")
            binaries.append((_found[0], f"nvidia/{_pkg}/bin"))

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
    [],
    exclude_binaries=True,
    name="MinecraftTranslator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX выключен: сжатый им exe чаще принимают за вирус.
    upx=False,
    upx_exclude=[],
    console=False,          # оконное приложение
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=version_info,
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
