"""
Detección de GPU única para todo el programa (asistente de instalación y Querencia).

Orden para identificar la tarjeta y su VRAM total (se resuelve una vez por sesión):
  1. NVML (pynvml)        NVIDIA: nombre, total y libre exactos en cada consulta
  2. nvidia-smi           NVIDIA sin pynvml
  3. registro de Windows  AMD / Intel (y NVIDIA sin controlador completo):
                          HardwareInformation.qwMemorySize de cada adaptador; se elige el de más VRAM
                          y se ignoran los integrados con poca memoria y los adaptadores virtuales.
VRAM libre en AMD/Intel: contador de rendimiento «GPU Adapter Memory(*)\\Dedicated Usage» leído con
PDH (PdhAddEnglishCounterW acepta el nombre en inglés aunque Windows esté en español; no abre
procesos ni ventanas). Si no se puede leer, libre = total − margen prudente y `free_estimated`.

Las funciones _nvml_read, _smi_read, _registry_adapters y _pdh_dedicated_usage están separadas
para poder simularlas en pruebas (sin tener la tarjeta).
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass

MiB = 1024 * 1024
GiB = 1024 * MiB
MIN_DEDICATED = 1 * GiB                    # por debajo: integrada o adaptador virtual, no cuenta
DRIVER_RESERVE = 192 * MiB                 # lo que el contador no ve y NVML sí (reservado del controlador)
VENDORS = {"10de": "nvidia", "1002": "amd", "1022": "amd", "8086": "intel"}
SKIP_NAMES = ("microsoft basic", "remote display", "parsec", "virtual", "citrix", "vmware", "hyper-v")


@dataclass
class GPUInfo:
    name: str = ""
    vendor: str = ""                       # nvidia | amd | intel | "" (desconocido)
    total: int = 0                         # bytes
    free: int = 0                          # bytes
    source: str = ""                       # de dónde salen los datos (se muestra en /mem y /doctor)
    free_estimated: bool = False           # True si no se pudo medir la VRAM libre

    @property
    def used(self) -> int:
        return max(0, self.total - self.free)

    @property
    def found(self) -> bool:
        return self.total > 0


def vendor_from(name: str, pci: str = "") -> str:
    m = pci.lower()
    for vid, v in VENDORS.items():
        if f"ven_{vid}" in m:
            return v
    n = name.lower()
    if any(k in n for k in ("nvidia", "geforce", "rtx", "quadro", "tesla")):
        return "nvidia"
    if any(k in n for k in ("amd", "radeon", "firepro")):
        return "amd"
    if "intel" in n or " arc " in f" {n} ":
        return "intel"
    return ""


def estimated_margin(total: int) -> int:
    """VRAM que se da por ocupada cuando no se puede medir (escritorio, navegador, otras apps)."""
    return max(1 * GiB, int(total * 0.10))


# ---------------------------------------------------------------------------
# Fuentes (cada una se puede simular en pruebas)
# ---------------------------------------------------------------------------

_nvml_handle = None
_nvml_failed = False


def _nvml_read() -> tuple[str, int, int] | None:
    """(nombre, total, libre) por NVML, o None."""
    global _nvml_handle, _nvml_failed
    if _nvml_failed:
        return None
    try:
        import pynvml
        if _nvml_handle is None:
            pynvml.nvmlInit()
            _nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        mi = pynvml.nvmlDeviceGetMemoryInfo(_nvml_handle)
        name = pynvml.nvmlDeviceGetName(_nvml_handle)
        return (name.decode() if isinstance(name, bytes) else str(name)), int(mi.total), int(mi.free)
    except Exception:
        _nvml_failed = True
        return None


def _smi_read() -> tuple[str, int, int] | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip().splitlines()[0]
        name, tot, free = [x.strip() for x in out.split(",")]
        return name, int(tot) * MiB, int(free) * MiB
    except Exception:
        return None


CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def _registry_adapters() -> list[dict]:
    """Adaptadores de pantalla del registro: [{"name", "bytes", "pci"}]."""
    if sys.platform != "win32":
        return []
    import winreg
    out = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CLASS_KEY) as cls:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(cls, i)
                except OSError:
                    break
                i += 1
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(cls, sub) as k:
                        def val(n):
                            try:
                                return winreg.QueryValueEx(k, n)[0]
                            except OSError:
                                return None
                        size = val("HardwareInformation.qwMemorySize")
                        if size is None:                       # controladores antiguos: DWORD o binario
                            size = val("HardwareInformation.MemorySize")
                        if isinstance(size, (bytes, bytearray)):
                            size = int.from_bytes(bytes(size)[:8], "little")
                        out.append({"name": str(val("DriverDesc") or ""), "bytes": int(size or 0),
                                    "pci": str(val("MatchingDeviceId") or "")})
                except OSError:
                    continue
    except OSError:
        pass
    return out


_pdh = {"query": None, "counter": None, "failed": False}


def _pdh_dedicated_usage() -> dict[str, int] | None:
    """VRAM dedicada usada por adaptador (LUID → bytes), sumando sus instancias «phys_N»."""
    if sys.platform != "win32" or _pdh["failed"]:
        return None
    import ctypes
    from ctypes import wintypes
    try:
        pdh = ctypes.WinDLL("pdh")
        if _pdh["query"] is None:
            q, c = ctypes.c_void_p(), ctypes.c_void_p()
            if pdh.PdhOpenQueryW(None, None, ctypes.byref(q)) != 0:
                raise OSError("PdhOpenQuery")
            if pdh.PdhAddEnglishCounterW(q, "\\GPU Adapter Memory(*)\\Dedicated Usage", None, ctypes.byref(c)) != 0:
                raise OSError("PdhAddEnglishCounter")
            _pdh["query"], _pdh["counter"] = q, c
        if pdh.PdhCollectQueryData(_pdh["query"]) != 0:
            return None

        class FMT(ctypes.Structure):
            _fields_ = [("CStatus", wintypes.DWORD), ("largeValue", ctypes.c_longlong)]

        class ITEM(ctypes.Structure):
            _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", FMT)]

        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        pdh.PdhGetFormattedCounterArrayW(_pdh["counter"], 0x400, ctypes.byref(size), ctypes.byref(count), None)
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if pdh.PdhGetFormattedCounterArrayW(_pdh["counter"], 0x400, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return None
        usage: dict[str, int] = {}
        for it in ctypes.cast(buf, ctypes.POINTER(ITEM * count.value)).contents:
            luid = "_".join(str(it.szName).split("_")[:3])          # luid_0x..._0x...
            usage[luid] = usage.get(luid, 0) + max(0, int(it.FmtValue.largeValue))
        return usage
    except Exception:
        _pdh["failed"] = True
        return None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

_static: GPUInfo | None = None            # tarjeta y total: una vez por sesión
_free_cache: tuple[float, int | None] = (0.0, None)
_lock = threading.Lock()


def _identify() -> GPUInfo:
    r = _nvml_read()
    if r:
        return GPUInfo(r[0], "nvidia", r[1], r[2], "NVML")
    r = _smi_read()
    if r:
        return GPUInfo(r[0], "nvidia", r[1], r[2], "nvidia-smi")
    cands = [a for a in _registry_adapters()
             if a["bytes"] >= MIN_DEDICATED and not any(s in a["name"].lower() for s in SKIP_NAMES)]
    if cands:
        best = max(cands, key=lambda a: a["bytes"])
        return GPUInfo(best["name"], vendor_from(best["name"], best["pci"]), best["bytes"], 0, "registro de Windows")
    return GPUInfo(source="sin GPU dedicada detectada")


def _registry_free(total: int, max_age: float) -> tuple[int, bool]:
    """(libre, estimada) para GPUs detectadas por el registro; contador cacheado max_age segundos."""
    global _free_cache
    now = time.monotonic()
    if _free_cache[1] is None or now - _free_cache[0] > max_age:
        usage = _pdh_dedicated_usage()
        # varios adaptadores (p. ej. integrada + dedicada): se toma el que más usa, que es lo prudente
        # (nunca se sobreestima la VRAM libre de la dedicada)
        used = max(usage.values()) if usage else None
        _free_cache = (now, used)
    used = _free_cache[1]
    if used is None:
        return max(0, total - estimated_margin(total)), True
    return max(0, total - used - DRIVER_RESERVE), False


def info(max_age: float = 2.0) -> GPUInfo:
    """Tarjeta principal con VRAM total y libre. Barato de llamar a menudo (cachea lo costoso)."""
    global _static
    with _lock:
        if _static is None:
            _static = _identify()
        s = _static
        if not s.found:
            return GPUInfo(source=s.source)
        if s.source == "NVML":
            r = _nvml_read()
            if r:
                return GPUInfo(r[0], "nvidia", r[1], r[2], "NVML")
        if s.source == "nvidia-smi":
            r = _smi_read()
            if r:
                return GPUInfo(r[0], "nvidia", r[1], r[2], "nvidia-smi")
        free, est = _registry_free(s.total, max_age)
        src = "registro de Windows · " + ("VRAM libre estimada (sin contador)" if est else "contador de rendimiento")
        return GPUInfo(s.name, s.vendor, s.total, free, src, est)


VENDOR_NAME = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel"}


def engine_advice(vendor: str) -> tuple[str, list[str]]:
    """(resumen, pasos) para instalar llama-cpp-python con aceleración en esta GPU."""
    if vendor in ("amd", "intel"):
        v = VENDOR_NAME[vendor]
        return (f"Con una GPU {v}, «pip install llama-cpp-python» instala la versión SOLO CPU: necesitas una compilada "
                "con Vulkan.", [
                    "Instala el Vulkan SDK (vulkan.lunarg.com) y Visual Studio Build Tools con «Desarrollo para el "
                    "escritorio con C++».",
                    'En PowerShell: $env:CMAKE_ARGS="-DGGML_VULKAN=on"; pip install llama-cpp-python --no-cache-dir '
                    "--force-reinstall",
                    "Sin compilar: instala Ollama (ollama.com), que usa la GPU si tu modelo de tarjeta está soportado, "
                    "y elige sus modelos con /model.",
                ])
    if vendor == "nvidia":
        return ("Con NVIDIA sirve la versión Vulkan, y la CUDA es algo más rápida leyendo prompts.", [
            "Wheel precompilada CUDA: busca una «cu12x» para tu versión de Python (o compila con "
            '$env:CMAKE_ARGS="-DGGML_CUDA=on").',
            'Vulkan: Vulkan SDK + $env:CMAKE_ARGS="-DGGML_VULKAN=on"; pip install llama-cpp-python --no-cache-dir '
            "--force-reinstall",
        ])
    return ("Sin GPU dedicada, la versión normal (solo CPU) es la adecuada.", ["pip install llama-cpp-python"])


def reset():
    """Olvida lo detectado (pruebas, o tras cambiar de controlador)."""
    global _static, _free_cache, _nvml_handle, _nvml_failed
    with _lock:
        _static, _free_cache, _nvml_handle, _nvml_failed = None, (0.0, None), None, False
        _pdh.update(query=None, counter=None, failed=False)
