"""
Detección del hardware y estimación de velocidad de inferencia.

La generación de texto en local está limitada sobre todo por el ANCHO DE BANDA de
memoria: por cada token hay que leer los pesos activos del modelo. Por eso:

  tiempo por token ≈ 2 ms + bytes_en_GPU / (BW_GPU · eficiencia) + bytes_en_RAM / (BW_RAM · eficiencia)

Eficiencias calibradas en este proyecto (RTX 3070 + DDR4-3600 doble canal, llama.cpp Vulkan):
  denso en GPU 0.55 · denso en CPU 0.50 · MoE en GPU 0.45 · MoE en CPU 0.18
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field

GPU_BANDWIDTH = {  # GB/s (memoria de vídeo)
    "gtx 1650": 128, "gtx 1660": 192, "rtx 2060": 336, "rtx 2070": 448, "rtx 2080 ti": 616, "rtx 2080": 448,
    "rtx 3050": 224, "rtx 3060 ti": 448, "rtx 3060": 360, "rtx 3070 ti": 608, "rtx 3070": 448,
    "rtx 3080 ti": 912, "rtx 3080": 760, "rtx 3090 ti": 1008, "rtx 3090": 936,
    "rtx 4060 ti": 288, "rtx 4060": 272, "rtx 4070 ti super": 672, "rtx 4070 ti": 504, "rtx 4070 super": 504,
    "rtx 4070": 504, "rtx 4080 super": 736, "rtx 4080": 717, "rtx 4090": 1008,
    "rtx 5060 ti": 448, "rtx 5060": 448, "rtx 5070 ti": 896, "rtx 5070": 672, "rtx 5080": 960, "rtx 5090": 1792,
    "rx 6600": 224, "rx 6700 xt": 384, "rx 6800 xt": 512, "rx 6800": 512, "rx 6900 xt": 512, "rx 7600": 288,
    "rx 7700 xt": 432, "rx 7800 xt": 624, "rx 7900 xtx": 960, "rx 7900 xt": 800, "rx 9070 xt": 640, "rx 9070": 640,
    "arc a770": 560, "arc a750": 512, "arc b580": 456, "rtx a6000": 768, "rtx 6000 ada": 960,
    "rtx pro 6000": 1792, "a100": 2039, "h100": 3350, "l40s": 864,
}

MEM_TYPES = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 34: "DDR5", 35: "LPDDR5"}


@dataclass
class Hardware:
    cpu_name: str = "?"
    cpu_cores: int = 0
    cpu_threads: int = 0
    cpu_ghz: float = 0.0
    ram_gb: float = 0.0
    ram_type: str = "?"
    ram_mts: int = 0                # MT/s (p. ej. DDR4-3600 -> 3600)
    ram_channels: int = 2
    gpu_name: str = ""
    vram_gb: float = 0.0
    gpu_bw: float = 0.0             # GB/s
    disk_free_gb: float = 0.0
    detected: list[str] = field(default_factory=list)

    @property
    def ram_bw(self) -> float:
        """Ancho de banda teórico de la RAM en GB/s (MT/s × 8 bytes × canales)."""
        mts = self.ram_mts or 3200
        return mts * 8 * max(1, self.ram_channels) / 1000

    @property
    def has_gpu(self) -> bool:
        return self.vram_gb >= 2 and self.gpu_bw > 0

    @property
    def vram_usable(self) -> float:
        return max(0.0, self.vram_gb - 1.0) if self.has_gpu else 0.0

    @property
    def ram_usable(self) -> float:
        """RAM que razonablemente puede usar un modelo (deja sitio al sistema y a otras apps)."""
        return max(0.0, self.ram_gb * 0.80 - 3.0)

    def to_json(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        g = f"{self.gpu_name} {self.vram_gb:.0f} GB (~{self.gpu_bw:.0f} GB/s)" if self.has_gpu else "sin GPU dedicada"
        return (f"{self.ram_gb:.0f} GB {self.ram_type}-{self.ram_mts or '?'} ×{self.ram_channels} canales "
                f"(~{self.ram_bw:.0f} GB/s) · {g} · CPU {self.cpu_cores} núcleos {self.cpu_ghz:.1f} GHz")


def _ps(cmd: str) -> str:
    try:
        return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True,
                              timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return ""


def gpu_bandwidth_for(name: str) -> float:
    n = name.lower()
    for key in sorted(GPU_BANDWIDTH, key=len, reverse=True):
        if key in n:
            return float(GPU_BANDWIDTH[key])
    return 0.0


def detect(models_dir=None) -> Hardware:
    hw = Hardware()
    # --- CPU, RAM (Windows: WMI) ------------------------------------------------
    if sys.platform == "win32":
        out = _ps("Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,"
                  "MaxClockSpeed | ConvertTo-Json -Compress")
        try:
            d = json.loads(out)
            d = d[0] if isinstance(d, list) else d
            hw.cpu_name = str(d.get("Name", "?")).strip()
            hw.cpu_cores = int(d.get("NumberOfCores") or 0)
            hw.cpu_threads = int(d.get("NumberOfLogicalProcessors") or 0)
            hw.cpu_ghz = round(int(d.get("MaxClockSpeed") or 0) / 1000, 2)
            hw.detected.append("cpu")
        except Exception:
            pass
        out = _ps("Get-CimInstance Win32_PhysicalMemory | Select-Object BankLabel,DeviceLocator,Capacity,Speed,"
                  "ConfiguredClockSpeed,SMBIOSMemoryType | ConvertTo-Json -Compress")
        try:
            mods = json.loads(out)
            mods = mods if isinstance(mods, list) else [mods]
            hw.ram_gb = round(sum(int(m.get("Capacity") or 0) for m in mods) / 2 ** 30, 1)
            speeds = [int(m.get("ConfiguredClockSpeed") or m.get("Speed") or 0) for m in mods]
            hw.ram_mts = max(speeds) if speeds else 0
            hw.ram_type = MEM_TYPES.get(int(mods[0].get("SMBIOSMemoryType") or 0), "?")
            chans = {re.search(r"CHANNEL\s*([A-Z])", f"{m.get('BankLabel', '')} {m.get('DeviceLocator', '')}", re.I)
                     for m in mods}
            letters = {c.group(1).upper() for c in chans if c}
            hw.ram_channels = len(letters) if letters else min(2, len(mods))
            hw.detected.append("ram")
        except Exception:
            pass
    if not hw.ram_gb:
        try:
            import psutil
            hw.ram_gb = round(psutil.virtual_memory().total / 2 ** 30, 1)
        except Exception:
            pass
    if not hw.cpu_cores:
        import os
        hw.cpu_threads = os.cpu_count() or 0
        hw.cpu_cores = max(1, hw.cpu_threads // 2)
    # --- GPU (misma detección que usa el programa: NVML → nvidia-smi → registro) ---
    from . import gpu
    g = gpu.info()
    if g.found:
        hw.gpu_name = g.name
        hw.vram_gb = round(g.total / 2 ** 30, 1)
        hw.detected.append("gpu")
    hw.gpu_bw = gpu_bandwidth_for(hw.gpu_name) if hw.gpu_name else 0.0
    # --- disco ---------------------------------------------------------------
    try:
        import shutil
        from .config import MODELS_DIR
        hw.disk_free_gb = round(shutil.disk_usage(str(models_dir or MODELS_DIR.parent)).free / 2 ** 30, 1)
    except Exception:
        pass
    return hw


# ---------------------------------------------------------------------------
# Estimaciones
# ---------------------------------------------------------------------------

@dataclass
class Fit:
    fits: bool
    gpu_frac: float          # parte del modelo en VRAM (0–1)
    tps_low: float
    tps_high: float
    mem_needed: float        # GB totales (modelo + caché + mmproj)
    where: str               # "GPU" | "GPU+RAM" | "CPU"

    @property
    def tps(self) -> float:
        return (self.tps_low + self.tps_high) / 2


def estimate(hw: Hardware, size_gb: float, params_b: float, active_b: float | None = None,
             extra_gb: float = 0.0, ctx_k: int = 16) -> Fit:
    """Cabe / velocidad aproximada de un GGUF en este hardware (contexto ctx_k miles de tokens)."""
    moe = bool(active_b and active_b < params_b * 0.6)
    kv = 0.4 + size_gb * 0.03 * (ctx_k / 16)            # caché KV + buffers (aprox.)
    need = size_gb + kv + extra_gb
    vram = hw.vram_usable
    gpu_part = min(need, vram)
    cpu_part = need - gpu_part
    fits = cpu_part <= hw.ram_usable
    gpu_frac = min(1.0, max(0.0, (vram - kv - extra_gb) / size_gb)) if hw.has_gpu else 0.0
    per_token = size_gb * ((active_b / params_b) if moe else 1.0)
    g_bytes = per_token * gpu_frac
    c_bytes = per_token - g_bytes
    eg, ec = (0.45, 0.18) if moe else (0.55, 0.50)
    t = 0.002
    if g_bytes:
        t += g_bytes / (max(hw.gpu_bw, 1) * eg)
    if c_bytes:
        t += c_bytes / (max(hw.ram_bw, 1) * ec)
    tps = 1 / t
    where = "GPU" if gpu_frac >= 0.999 else ("GPU+RAM" if gpu_frac > 0 else "CPU")
    return Fit(fits, gpu_frac, round(tps * 0.7, 1), round(tps * 1.3, 1), round(need, 1), where)


def speed_words(tps: float) -> str:
    if tps >= 40:
        return "instantáneo"
    if tps >= 20:
        return "fluido"
    if tps >= 8:
        return "cómodo (más rápido que leer)"
    if tps >= 3:
        return "lento (como leer en voz alta)"
    if tps >= 1:
        return "muy lento (para tareas que dejas trabajando)"
    return "impracticable"


def tier_of(hw: Hardware) -> str:
    total = hw.vram_usable + hw.ram_usable
    if total < 10:
        return "equipo ligero (modelos pequeños, sobre todo utilidades)"
    if total < 28:
        return "equipo medio (modelos de 4–12B fluidos; MoE de ~30B usables)"
    if total < 64:
        return "equipo potente (MoE de 30–35B fluidos; densos de 27–32B usables)"
    if total < 160:
        return "estación de trabajo (MoE de 80–120B)"
    if total < 400:
        return "servidor (MoE de 200–400B)"
    return "mini centro de datos (modelos de frontera abiertos, 400B+)"
