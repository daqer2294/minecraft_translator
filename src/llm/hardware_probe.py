# src/llm/hardware_probe.py
from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from dataclasses import dataclass, asdict
from typing import Optional, Tuple

from .. import config

# =============================================================================
# Лёгкая проба железа: определяем GPU/VRAM, RAM, ядра CPU и рекомендуем тир.
# Тяжёлые зависимости (torch) НЕ тянем: GPU определяем через nvidia-smi / платформу,
# RAM/CPU — через psutil (если установлен) с чистым stdlib-фоллбеком.
# Результат кэшируется в config.HARDWARE_CACHE_PATH.
# =============================================================================

# Пороги рекомендации тира
_STD_MIN_VRAM_MB = 6000
_STD_MIN_CORES = 8
_STD_MIN_RAM_MB = 16000


@dataclass
class HardwareInfo:
    gpu_available: bool
    gpu_name: str
    gpu_vram_mb: int
    ram_mb: int
    cpu_cores: int
    recommended_tier: str      # "light" | "standard"
    probed_at: float
    source: str = ""           # как определили (для диагностики)

    def summary(self) -> str:
        gpu = f"{self.gpu_name} ({self.gpu_vram_mb} MB)" if self.gpu_available else "нет"
        return (
            f"GPU: {gpu} | RAM: {self.ram_mb} MB | CPU: {self.cpu_cores} ядер "
            f"→ рекомендуемый тир: {self.recommended_tier}"
        )


# ---------------- GPU ----------------

def _detect_nvidia() -> Optional[Tuple[str, int]]:
    """Вернуть (name, vram_mb) через nvidia-smi или None."""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    # берём первую (самую мощную обычно) карту
    line = out.stdout.strip().splitlines()[0]
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 2:
        return None
    name = parts[0]
    try:
        vram = int(float(parts[1]))  # МиБ
    except ValueError:
        return None
    return name, vram


def _detect_apple_metal(ram_mb: int) -> Optional[Tuple[str, int]]:
    """
    Определение GPU на macOS.

    • Apple Silicon (arm64): память унифицирована (CPU+GPU), Metal доступен —
      считаем «GPU VRAM» равным объёму RAM (приближение unified memory).

    • Intel Mac (x86_64): СОЗНАТЕЛЬНО НЕ ПОДДЕРЖИВАЕМ авто-детект дискретного/
      внешнего GPU (AMD/eGPU). Парсинг `system_profiler SPDisplaysDataType`
      ненадёжен и требует отдельного тестирования на реальном железе, которого
      нет в CI. Поэтому Intel Mac трактуется как «без GPU» и попадает в тир по
      CPU/RAM-порогам (обычно light). Пользователь всегда может вручную выбрать
      standard в GUI. Nvidia на Mac неактуальна (нет драйверов в новых macOS).
    """
    if platform.system() == "Darwin" and platform.machine().lower() in ("arm64", "aarch64"):
        return "Apple Silicon (Metal, unified memory)", ram_mb
    # Intel Mac и всё прочее на Darwin → GPU не детектим (см. docstring)
    return None


# ---------------- RAM / CPU ----------------

def _detect_ram_cpu() -> Tuple[int, int, str]:
    """Вернуть (ram_mb, cpu_cores, source)."""
    # 1) psutil (точнее всего, но опциональный)
    try:
        import psutil  # type: ignore
        ram_mb = int(psutil.virtual_memory().total / (1024 * 1024))
        cores = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 1
        return ram_mb, int(cores), "psutil"
    except Exception:
        pass

    # 2) stdlib fallback
    cores = os.cpu_count() or 1
    ram_mb = 0
    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names and "SC_PAGE_SIZE" in os.sysconf_names:
            ram_mb = int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024))
        elif platform.system() == "Windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            ram_mb = int(stat.ullTotalPhys / (1024 * 1024))
    except Exception:
        ram_mb = 0
    return ram_mb, int(cores), "stdlib"


# ---------------- рекомендация ----------------

def recommend_tier(gpu_vram_mb: int, cpu_cores: int, ram_mb: int) -> str:
    """
    Простые пороги:
      - GPU c 6GB+ VRAM → standard;
      - иначе, если 8+ ядер и 16GB+ RAM → standard;
      - иначе → light.
    """
    if gpu_vram_mb >= _STD_MIN_VRAM_MB:
        return "standard"
    if cpu_cores >= _STD_MIN_CORES and ram_mb >= _STD_MIN_RAM_MB:
        return "standard"
    return "light"


# ---------------- публичный API ----------------

def probe_hardware() -> HardwareInfo:
    """Активная проба железа (без чтения кэша)."""
    ram_mb, cores, ram_src = _detect_ram_cpu()

    gpu_name = ""
    vram = 0
    gpu_available = False
    gpu_src = "none"

    nv = _detect_nvidia()
    if nv:
        gpu_name, vram = nv
        gpu_available = True
        gpu_src = "nvidia-smi"
    else:
        ap = _detect_apple_metal(ram_mb)
        if ap:
            gpu_name, vram = ap
            gpu_available = True
            gpu_src = "apple-metal"

    tier = recommend_tier(vram, cores, ram_mb)
    return HardwareInfo(
        gpu_available=gpu_available,
        gpu_name=gpu_name,
        gpu_vram_mb=vram,
        ram_mb=ram_mb,
        cpu_cores=cores,
        recommended_tier=tier,
        probed_at=time.time(),
        source=f"gpu={gpu_src};ram={ram_src}",
    )


def load_cached() -> Optional[HardwareInfo]:
    """Загрузить прошлую пробу из hardware.json (или None)."""
    path = config.HARDWARE_CACHE_PATH
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return HardwareInfo(**data)
    except Exception:
        return None


def save_cache(info: HardwareInfo) -> None:
    """Сохранить пробу в hardware.json (ошибки не критичны)."""
    try:
        config.ensure_app_dirs()
        os.makedirs(os.path.dirname(config.HARDWARE_CACHE_PATH) or ".", exist_ok=True)
        with open(config.HARDWARE_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(info), f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_or_probe(force: bool = False) -> HardwareInfo:
    """
    Вернуть кэшированную пробу; если её нет или force=True — прогнать пробу
    и сохранить результат.
    """
    if not force:
        cached = load_cached()
        if cached is not None:
            return cached
    info = probe_hardware()
    save_cache(info)
    return info
