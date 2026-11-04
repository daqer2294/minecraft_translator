# src/modpack_info.py
"""Версия Minecraft и загрузчик сборки, формат ресурспака для этой версии."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# Формат ресурспаков по версиям. Проверено 2026-11-04 по двум источникам:
# https://minecraft.wiki/w/Pack_format и misode/mcmeta (versions/data.min.json),
# для 1.20.1 и 1.21.1 ещё и по version.json клиентского jar.
RESOURCE_PACK_FORMATS = {
    "1.20": 15, "1.20.1": 15, "1.20.2": 18, "1.20.3": 22, "1.20.4": 22, "1.20.5": 32, "1.20.6": 32,
    "1.21": 34, "1.21.1": 34, "1.21.2": 42, "1.21.3": 42, "1.21.4": 46, "1.21.5": 55, "1.21.6": 63,
    "1.21.7": 64, "1.21.8": 64, "1.21.9": 69, "1.21.10": 69, "1.21.11": 75,
    "26.1": 84, "26.1.1": 84, "26.1.2": 84, "26.2": 88, "26.3": 97,
}
DEFAULT_PACK_FORMAT = 34

_METADATA_SEARCH_LEVELS = 3
_LOADER_NAMES = {"neoforge": "NeoForge", "forge": "Forge", "fabric": "Fabric", "quilt": "Quilt"}


@dataclass
class ModpackInfo:
    minecraft: Optional[str] = None
    loader: Optional[str] = None
    loader_version: Optional[str] = None
    source: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def pack_format(self) -> int:
        return RESOURCE_PACK_FORMATS.get(self.minecraft or "", DEFAULT_PACK_FORMAT)

    @property
    def format_is_known(self) -> bool:
        return self.minecraft in RESOURCE_PACK_FORMATS

    def describe(self) -> str:
        if not self.minecraft and not self.loader:
            return "версия и загрузчик не определены"
        loader = _LOADER_NAMES.get(self.loader or "", self.loader or "загрузчик неизвестен")
        version = f" {self.loader_version}" if self.loader_version else ""
        return f"Minecraft {self.minecraft or '?'}, {loader}{version} (из {self.source})"


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _split_loader(name: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """'neoforge-21.1.265' и 'fabric-0.18.1-1.21.1' → (загрузчик, его версия)."""
    if not isinstance(name, str) or not name:
        return None, None
    parts = name.split("-")
    return parts[0].lower(), parts[1] if len(parts) > 1 else None


def _from_instance(data: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    base = data.get("baseModLoader") or {}
    loader, version = _split_loader(base.get("name"))
    return base.get("minecraftVersion") or data.get("gameVersion"), loader, version


def _from_manifest(data: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    minecraft = data.get("minecraft") or {}
    loaders = minecraft.get("modLoaders") or []
    primary = next((x for x in loaders if isinstance(x, dict) and x.get("primary")), None)
    if primary is None and loaders and isinstance(loaders[0], dict):
        primary = loaders[0]
    loader, version = _split_loader((primary or {}).get("id"))
    return minecraft.get("version"), loader, version


def _read_folder(folder: str) -> Optional[ModpackInfo]:
    """minecraftinstance.json описывает то, что установлено, поэтому он важнее manifest.json."""
    instance = _read_json(os.path.join(folder, "minecraftinstance.json"))
    manifest = _read_json(os.path.join(folder, "manifest.json"))
    from_instance = _from_instance(instance) if instance else (None, None, None)
    from_manifest = _from_manifest(manifest) if manifest else (None, None, None)
    if not any(from_instance) and not any(from_manifest):
        return None

    info = ModpackInfo(
        minecraft=from_instance[0] or from_manifest[0],
        loader=from_instance[1] or from_manifest[1],
        loader_version=from_instance[2] or from_manifest[2],
        source="minecraftinstance.json" if any(from_instance) else "manifest.json",
    )
    if any(from_instance) and any(from_manifest):
        if from_instance[:2] != from_manifest[:2]:
            info.warnings.append(
                f"manifest.json и minecraftinstance.json расходятся: {from_manifest[:2]} и {from_instance[:2]}"
            )
    return info


def detect_modpack(base_input: str) -> ModpackInfo:
    """Ищет метаданные в выбранной папке и выше: можно выбрать и mods внутри сборки."""
    folder = os.path.abspath(base_input)
    for _ in range(_METADATA_SEARCH_LEVELS + 1):
        info = _read_folder(folder)
        if info is not None:
            if not info.format_is_known:
                info.warnings.append(
                    f"формат ресурспака для Minecraft {info.minecraft} неизвестен, "
                    f"указан {DEFAULT_PACK_FORMAT}: игра покажет предупреждение о совместимости"
                )
            return info
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return ModpackInfo(warnings=[
        f"manifest.json и minecraftinstance.json не найдены, формат ресурспака {DEFAULT_PACK_FORMAT} (Minecraft 1.21.1)"
    ])
